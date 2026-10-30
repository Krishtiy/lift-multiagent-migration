"""Pydantic schemas for Lift REST API request and response validation."""

from datetime import datetime
from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class JobCreateRequest(BaseModel):
    recipe: str = Field(default="py2_to_py3", description="Migration recipe identifier.")
    repo_path: str = Field(description="Local path or git URL to the repository.")
    token_budget: int = Field(default=500_000, description="Maximum token budget for this job.")


class JobStatusResponse(BaseModel):
    id: str
    recipe: str
    repo_reference: str
    status: str
    token_budget: int
    tokens_used: int
    cost_usd: float
    created_at: datetime
    updated_at: datetime
    chunk_counts: Dict[str, int] = Field(default_factory=dict)


class ChunkResponse(BaseModel):
    id: str
    job_id: str
    file_path: str
    dependency_level: int
    status: str
    attempt_count: int
    review_reason: Optional[str] = None
    lease_owner: Optional[str] = None
    lease_expires_at: Optional[datetime] = None


class AgentCallResponse(BaseModel):
    id: str
    job_id: str
    chunk_id: Optional[str] = None
    agent_name: str
    model: str
    prompt_hash: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: int
    tool_name: Optional[str] = None
    ok_flag: bool
    created_at: datetime


class HumanReviewActionResponse(BaseModel):
    chunk_id: str
    new_status: str
    message: str
