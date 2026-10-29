# Lift: Multi-Agent Legacy Code Migration System

A deterministic, multi-agent legacy migration platform backed by characterization testing ("golden master" approach) and Postgres concurrency control.

## 1. Project Overview

Lift converts legacy applications to modern stacks chunk-by-chunk using a team of specialized agents coordinated by a deterministic orchestrator. Real execution of test suites recorded on the old code decides correctness—never the LLM alone.

### Migration Recipes
1. **Python 2 to Python 3**: Same-language syntactic and runtime modernization (`/` division, strings vs bytes, iteration models, exception syntax).
2. **Express to FastAPI**: Cross-language framework migration (REST routes, JSON validation, HTTP error status models, dependency injection).

---

## 2. Success Metrics

1. **First-Try Pass Rate**: % of chunks that pass characterization and held-out tests on attempt 1.
2. **Pass Rate After Retries**: % of chunks that pass within `MAX_ATTEMPTS` (3 attempts max).
3. **Human-Review Rate**: % of chunks routed to `NEEDS_REVIEW` (with machine-readable reasons).
4. **Regression Rate**: % of earlier passing module tests broken by a new chunk migration.
5. **LLM Consumption**: Model calls, prompt tokens, completion tokens, and dollar cost per chunk/job.
6. **Execution Latency**: Wall-clock duration per chunk stage and total end-to-end migration time.

---

## 3. Explicit Non-Goals

- **No full-blown UI**: Read-only status dashboard / SSE stream only; backend reliability and test rigor are prioritized.
- **No auto-merging to production**: Migrations produce isolated branches/artifacts with comprehensive diff reports.
- **No third migration recipe**: Focus strictly on deep execution of Python 2 -> 3 and Express -> FastAPI.
- **No LLM orchestrator**: Orchestration logic (queue, locking, transitions, retries, rollback) is ordinary deterministic code.

---

## 4. Architecture Summary

```
POST /jobs (repo + recipe)
       │
[ FastAPI API ] <──── Status, Tracing, Chunks, Reports
       │
 [ Postgres DB ] ──── (State machine: jobs, chunks, attempts, agent_calls)
       ▲
       │ FOR UPDATE SKIP LOCKED
 [ Worker Process ]
       │
 [ Orchestrator ] ─── analyze ──> plan ──> record tests ──> rules pass ──> migrate ──> verify
       │                                                                      ▲           │
       │                                                                      └── retry ──┘
 ┌─────┴──────────────────────────────────┐        ┌──────────────────────────────┐
 │ Agents & Specialized Tools             │        │ Docker Sandbox               │
 │ - Analyzer (deterministic AST graph)   │        │ - Old & New isolated runtimes│
 │ - Planner (dependency chunking)        │        │ - No network access          │
 │ - Test-Writer (golden test generator)  │        │ - Memory, CPU & time limits  │
 │ - Migrator (code rewrite agent)        │        └──────────────────────────────┘
 │ - Verifier (deterministic test runner) │
 └────────────────────────────────────────┘
```

---

## 5. Repository Layout

```
lift/
├── lift/
│   ├── api/          # FastAPI REST endpoints & schemas
│   ├── core/         # Deterministic orchestrator, state machine, config
│   ├── agents/       # Analyzer, planner, test_writer, migrator, verifier
│   ├── llm/          # Unified provider interface, disk cache, cost tracking
│   ├── sandbox/      # Docker execution runner with isolation caps
│   ├── recipes/      # py2_to_py3 and express_to_fastapi modules
│   └── db/           # SQLAlchemy models and Alembic migrations
├── benchmarks/       # Seeded legacy applications and held-out test suites
├── experiments/      # Evaluation harness and benchmark result runners
├── tests/            # Automated test suite
├── docker-compose.yml
└── pyproject.toml
```
