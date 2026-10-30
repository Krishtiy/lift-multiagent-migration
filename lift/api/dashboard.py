"""Observability dashboard generator and analytics query helpers."""

import json
from typing import Any, Dict, List, Optional
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lift.db.models import AgentCall, Attempt, Chunk, ChunkStatus, Job


def compute_job_analytics(session: Session, job_id: str) -> Dict[str, Any]:
    """Calculate key observability metrics for a migration job.
    
    Answers:
    1. Which chunk cost the most?
    2. Which call / operation was the slowest?
    3. Which agent fails the most?
    4. Stage duration and token breakdown.
    """
    job = session.get(Job, job_id)
    if not job:
        return {}

    chunks = session.scalars(select(Chunk).where(Chunk.job_id == job_id)).all()
    calls = session.scalars(select(AgentCall).where(AgentCall.job_id == job_id)).all()
    attempts = session.scalars(
        select(Attempt).join(Chunk).where(Chunk.job_id == job_id)
    ).all()

    # 1. Chunk cost breakdown
    chunk_cost_map: Dict[str, float] = {}
    chunk_tokens_map: Dict[str, int] = {}
    for call in calls:
        cid = call.chunk_id or "unassigned"
        chunk_cost_map[cid] = chunk_cost_map.get(cid, 0.0) + call.cost_usd
        chunk_tokens_map[cid] = chunk_tokens_map.get(cid, 0) + (call.tokens_in + call.tokens_out)

    max_cost_chunk_id = max(chunk_cost_map, key=chunk_cost_map.get) if chunk_cost_map else None
    max_cost_chunk_path = "N/A"
    if max_cost_chunk_id:
        c_obj = session.get(Chunk, max_cost_chunk_id)
        if c_obj:
            max_cost_chunk_path = c_obj.file_path

    # 2. Slowest call / stage
    slowest_call = max(calls, key=lambda c: c.latency_ms) if calls else None

    # 3. Agent failure breakdown
    agent_failure_counts: Dict[str, int] = {}
    for call in calls:
        if not call.ok_flag:
            agent_failure_counts[call.agent_name] = agent_failure_counts.get(call.agent_name, 0) + 1

    most_failing_agent = (
        max(agent_failure_counts, key=agent_failure_counts.get)
        if agent_failure_counts
        else "None"
    )

    # Status counts
    status_counts: Dict[str, int] = {}
    for c in chunks:
        status_counts[c.status.value] = status_counts.get(c.status.value, 0) + 1

    return {
        "job_id": job.id,
        "recipe": job.recipe,
        "status": job.status.value,
        "total_chunks": len(chunks),
        "status_counts": status_counts,
        "tokens_used": job.tokens_used,
        "cost_usd": round(job.cost_usd, 4),
        "most_expensive_chunk": {
            "chunk_id": max_cost_chunk_id,
            "file_path": max_cost_chunk_path,
            "cost_usd": round(chunk_cost_map.get(max_cost_chunk_id, 0.0), 4) if max_cost_chunk_id else 0.0,
            "tokens": chunk_tokens_map.get(max_cost_chunk_id, 0) if max_cost_chunk_id else 0,
        },
        "slowest_stage": {
            "agent_name": slowest_call.agent_name if slowest_call else "N/A",
            "latency_ms": slowest_call.latency_ms if slowest_call else 0,
            "tool_name": slowest_call.tool_name if slowest_call else "N/A",
        },
        "agent_failure_summary": {
            "most_failing_agent": most_failing_agent,
            "failures_by_agent": agent_failure_counts,
        },
    }


def render_dashboard_html(job_id: str, analytics: Dict[str, Any], chunks: List[Dict[str, Any]]) -> str:
    """Render a responsive, modern HTML/CSS observability dashboard."""
    status_colors = {
        "PASSED": "#10b981",       # Emerald green
        "PENDING": "#64748b",      # Slate grey
        "TESTS_READY": "#0ea5e9",  # Sky blue
        "RULES_APPLIED": "#8b5cf6",# Violet
        "MIGRATING": "#f59e0b",    # Amber
        "VERIFYING": "#06b6d4",    # Cyan
        "RETRY": "#f97316",        # Orange
        "NEEDS_REVIEW": "#ef4444", # Red
        "FAILED": "#b91c1c",       # Dark Red
    }

    chunks_html = []
    for c in chunks:
        st = c.get("status", "PENDING")
        bg_col = status_colors.get(st, "#64748b")
        reason_badge = f'<div style="font-size:11px;color:#fca5a5;margin-top:4px;">⚠ {c.get("review_reason")}</div>' if c.get("review_reason") else ""
        actions_html = ""
        if st in ("NEEDS_REVIEW", "FAILED"):
            actions_html = f'''
            <div style="margin-top:8px;display:flex;gap:6px;">
                <button onclick="takeAction('{c["id"]}', 'retry')" style="background:#3b82f6;color:white;border:none;border-radius:4px;padding:3px 8px;font-size:11px;cursor:pointer;">Retry</button>
                <button onclick="takeAction('{c["id"]}', 'approve')" style="background:#10b981;color:white;border:none;border-radius:4px;padding:3px 8px;font-size:11px;cursor:pointer;">Approve</button>
            </div>'''

        chunks_html.append(f'''
        <div style="background:#1e293b;border:1px solid #334155;border-radius:8px;padding:12px;display:flex;flex-direction:column;justify-content:space-between;">
            <div>
                <div style="display:flex;justify-content:space-between;align-items:center;">
                    <span style="font-weight:600;font-size:13px;color:#f8fafc;word-break:break-all;">{c.get("file_path")}</span>
                    <span style="background:{bg_col};color:white;font-size:10px;font-weight:700;padding:2px 6px;border-radius:4px;">{st}</span>
                </div>
                <div style="font-size:12px;color:#94a3b8;margin-top:6px;">Level: {c.get("dependency_level", 0)} | Attempts: {c.get("attempt_count", 0)}</div>
                {reason_badge}
            </div>
            {actions_html}
        </div>''')

    chunks_grid = "\n".join(chunks_html) if chunks_html else '<div style="color:#94a3b8;">No chunks loaded yet.</div>'

    most_expensive = analytics.get("most_expensive_chunk", {})
    slowest_stage = analytics.get("slowest_stage", {})
    agent_failures = analytics.get("agent_failure_summary", {})

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Lift Migration Observability Dashboard - {job_id}</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
        body {{ background: #0f172a; color: #f8fafc; padding: 24px; }}
        .header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #334155; padding-bottom: 16px; margin-bottom: 24px; }}
        .title {{ font-size: 22px; font-weight: 700; letter-spacing: -0.5px; }}
        .sub {{ font-size: 13px; color: #94a3b8; margin-top: 4px; }}
        .kpi-row {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }}
        .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 10px; padding: 16px; }}
        .card-title {{ font-size: 12px; text-transform: uppercase; color: #94a3b8; letter-spacing: 0.5px; font-weight: 600; }}
        .card-value {{ font-size: 24px; font-weight: 700; margin-top: 6px; color: #38bdf8; }}
        .card-sub {{ font-size: 12px; color: #64748b; margin-top: 4px; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 14px; margin-top: 16px; }}
        .section-title {{ font-size: 16px; font-weight: 600; margin-bottom: 12px; color: #e2e8f0; }}
    </style>
</head>
<body>
    <div class="header">
        <div>
            <div class="title">Lift Migration Observability</div>
            <div class="sub">Job: <code>{job_id}</code> | Recipe: <code>{analytics.get("recipe", "py2_to_py3")}</code></div>
        </div>
        <div style="background:#0284c7;color:white;padding:6px 14px;border-radius:20px;font-size:12px;font-weight:600;">
            Status: {analytics.get("status", "RUNNING")}
        </div>
    </div>

    <!-- KPI ROW -->
    <div class="kpi-row">
        <div class="card">
            <div class="card-title">Total Chunks</div>
            <div class="card-value">{analytics.get("total_chunks", 0)}</div>
            <div class="card-sub">{analytics.get("status_counts", {}).get("PASSED", 0)} passed</div>
        </div>
        <div class="card">
            <div class="card-title">Token Consumption</div>
            <div class="card-value">{analytics.get("tokens_used", 0):,}</div>
            <div class="card-sub">Budget: 500,000</div>
        </div>
        <div class="card">
            <div class="card-title">LLM Cost</div>
            <div class="card-value">${analytics.get("cost_usd", 0.0):.4f}</div>
            <div class="card-sub">Cached reruns: $0.00</div>
        </div>
        <div class="card">
            <div class="card-title">Highest Cost Chunk</div>
            <div class="card-value" style="font-size:16px;color:#f59e0b;word-break:break-all;">{most_expensive.get("file_path", "N/A")}</div>
            <div class="card-sub">${most_expensive.get("cost_usd", 0.0):.4f} ({most_expensive.get("tokens", 0):,} tokens)</div>
        </div>
        <div class="card">
            <div class="card-title">Slowest Stage</div>
            <div class="card-value" style="font-size:18px;color:#a855f7;">{slowest_stage.get("agent_name", "N/A")}</div>
            <div class="card-sub">{slowest_stage.get("latency_ms", 0):,} ms</div>
        </div>
    </div>

    <!-- CHUNK GRID -->
    <div>
        <div class="section-title">Chunk Execution Grid</div>
        <div class="grid">
            {chunks_grid}
        </div>
    </div>

    <script>
        function takeAction(chunkId, action) {{
            fetch('/jobs/{job_id}/chunks/' + chunkId + '/' + action, {{ method: 'POST' }})
                .then(res => res.json())
                .then(data => {{
                    alert('Action ' + action + ' succeeded: ' + data.message);
                    window.location.reload();
                }})
                .catch(err => alert('Failed: ' + err));
        }}
    </script>
</body>
</html>"""
