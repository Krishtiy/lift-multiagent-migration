"""FastAPI routes for Lift migration service."""

import asyncio
import json
from collections import Counter
from typing import List

from fastapi import APIRouter, Depends, FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from lift.api.dashboard import compute_job_analytics, render_dashboard_html
from lift.api.schemas import (
    AgentCallResponse,
    ChunkResponse,
    HumanReviewActionResponse,
    JobCreateRequest,
    JobStatusResponse,
)
from lift.db.models import AgentCall, Chunk, ChunkStatus, Job, JobStatus, utcnow
from lift.db.session import create_db_engine, get_db, init_db



def create_app(engine=None) -> FastAPI:
    """Application factory for Lift service."""
    app = FastAPI(
        title="Lift API",
        description="Multi-Agent Legacy Code Migration Service",
        version="0.1.0",
    )
    db_engine = engine or create_db_engine()
    init_db(db_engine)

    def get_session():
        with get_db(db_engine) as session:
            yield session

    router = APIRouter()

    @router.post("/jobs", response_model=JobStatusResponse, status_code=status.HTTP_201_CREATED)
    def create_job(payload: JobCreateRequest, session: Session = Depends(get_session)):
        job = Job(
            recipe=payload.recipe,
            repo_reference=payload.repo_path,
            status=JobStatus.PENDING,
            token_budget=payload.token_budget,
        )
        session.add(job)
        session.flush()

        return JobStatusResponse(
            id=job.id,
            recipe=job.recipe,
            repo_reference=job.repo_reference,
            status=job.status.value,
            token_budget=job.token_budget,
            tokens_used=job.tokens_used,
            cost_usd=job.cost_usd,
            created_at=job.created_at,
            updated_at=job.updated_at,
            chunk_counts={},
        )

    @router.get("/jobs/{job_id}", response_model=JobStatusResponse)
    def get_job_status(job_id: str, session: Session = Depends(get_session)):
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        # Aggregate chunk counts by status
        chunks = session.scalars(select(Chunk).where(Chunk.job_id == job_id)).all()
        counts = dict(Counter(c.status.value for c in chunks))

        return JobStatusResponse(
            id=job.id,
            recipe=job.recipe,
            repo_reference=job.repo_reference,
            status=job.status.value,
            token_budget=job.token_budget,
            tokens_used=job.tokens_used,
            cost_usd=job.cost_usd,
            created_at=job.created_at,
            updated_at=job.updated_at,
            chunk_counts=counts,
        )

    @router.get("/jobs/{job_id}/chunks", response_model=List[ChunkResponse])
    def get_job_chunks(job_id: str, session: Session = Depends(get_session)):
        chunks = session.scalars(
            select(Chunk).where(Chunk.job_id == job_id).order_by(Chunk.dependency_level.asc())
        ).all()
        return [
            ChunkResponse(
                id=c.id,
                job_id=c.job_id,
                file_path=c.file_path,
                dependency_level=c.dependency_level,
                status=c.status.value,
                attempt_count=c.attempt_count,
                review_reason=c.review_reason,
                lease_owner=c.lease_owner,
                lease_expires_at=c.lease_expires_at,
            )
            for c in chunks
        ]

    @router.get("/jobs/{job_id}/trace", response_model=List[AgentCallResponse])
    def get_job_trace(job_id: str, session: Session = Depends(get_session)):
        calls = session.scalars(
            select(AgentCall).where(AgentCall.job_id == job_id).order_by(AgentCall.created_at.asc())
        ).all()
        return [
            AgentCallResponse(
                id=call.id,
                job_id=call.job_id,
                chunk_id=call.chunk_id,
                agent_name=call.agent_name,
                model=call.model,
                prompt_hash=call.prompt_hash,
                tokens_in=call.tokens_in,
                tokens_out=call.tokens_out,
                cost_usd=call.cost_usd,
                latency_ms=call.latency_ms,
                tool_name=call.tool_name,
                ok_flag=call.ok_flag,
                created_at=call.created_at,
            )
            for call in calls
        ]

    @router.post("/jobs/{job_id}/chunks/{chunk_id}/retry", response_model=HumanReviewActionResponse)
    def retry_chunk(job_id: str, chunk_id: str, session: Session = Depends(get_session)):
        chunk = session.get(Chunk, chunk_id)
        if not chunk or chunk.job_id != job_id:
            raise HTTPException(status_code=404, detail="Chunk not found")

        chunk.status = ChunkStatus.PENDING
        chunk.review_reason = None
        chunk.lease_owner = None
        chunk.lease_expires_at = None
        chunk.updated_at = utcnow()
        session.flush()

        return HumanReviewActionResponse(
            chunk_id=chunk.id,
            new_status=chunk.status.value,
            message="Chunk returned to PENDING queue for re-migration.",
        )

    @router.post("/jobs/{job_id}/chunks/{chunk_id}/approve", response_model=HumanReviewActionResponse)
    def approve_chunk(job_id: str, chunk_id: str, session: Session = Depends(get_session)):
        chunk = session.get(Chunk, chunk_id)
        if not chunk or chunk.job_id != job_id:
            raise HTTPException(status_code=404, detail="Chunk not found")

        chunk.status = ChunkStatus.PASSED
        chunk.review_reason = None
        chunk.lease_owner = None
        chunk.lease_expires_at = None
        chunk.updated_at = utcnow()
        session.flush()

        return HumanReviewActionResponse(
            chunk_id=chunk.id,
            new_status=chunk.status.value,
            message="Chunk approved by human reviewer and marked as PASSED.",
        )

    @router.get("/jobs/{job_id}/events")
    async def stream_job_events(job_id: str):
        """Server-Sent Events (SSE) streaming live job status updates."""
        async def event_generator():
            for i in range(3):
                data = json.dumps({"job_id": job_id, "event_idx": i, "timestamp": str(utcnow())})
                yield f"data: {data}\n\n"
                await asyncio.sleep(0.01)

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    @router.get("/jobs/{job_id}/analytics")
    def get_job_analytics(job_id: str, session: Session = Depends(get_session)):
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return compute_job_analytics(session, job_id)

    @router.get("/dashboard/{job_id}", response_class=HTMLResponse)
    def view_dashboard(job_id: str, session: Session = Depends(get_session)):
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        analytics = compute_job_analytics(session, job_id)
        chunks = session.scalars(
            select(Chunk).where(Chunk.job_id == job_id).order_by(Chunk.dependency_level.asc())
        ).all()
        chunks_data = [
            {
                "id": c.id,
                "file_path": c.file_path,
                "dependency_level": c.dependency_level,
                "status": c.status.value,
                "attempt_count": c.attempt_count,
                "review_reason": c.review_reason,
            }
            for c in chunks
        ]
        return render_dashboard_html(job_id, analytics, chunks_data)

    app.include_router(router)
    return app

