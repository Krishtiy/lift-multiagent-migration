"""SQLAlchemy models for Lift migration state machine."""

import enum
from datetime import datetime, timezone
from typing import List, Optional
import uuid

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def gen_uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class JobStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ChunkStatus(str, enum.Enum):
    PENDING = "PENDING"
    TESTS_READY = "TESTS_READY"
    RULES_APPLIED = "RULES_APPLIED"
    MIGRATING = "MIGRATING"
    VERIFYING = "VERIFYING"
    PASSED = "PASSED"
    RETRY = "RETRY"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    FAILED = "FAILED"


class ReviewReason(str, enum.Enum):
    TESTS_UNRECORDABLE = "tests_unrecordable"
    MAX_ATTEMPTS = "max_attempts"
    STUCK = "stuck"
    BUDGET = "budget"
    SANDBOX_ERROR = "sandbox_error"
    REGRESSION = "regression"


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    recipe: Mapped[str] = mapped_column(String(64), nullable=False)
    repo_reference: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus), nullable=False, default=JobStatus.PENDING
    )
    token_budget: Mapped[int] = mapped_column(Integer, default=500_000)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    chunks: Mapped[List["Chunk"]] = relationship("Chunk", back_populates="job", cascade="all, delete-orphan")
    agent_calls: Mapped[List["AgentCall"]] = relationship("AgentCall", back_populates="job", cascade="all, delete-orphan")
    artifacts: Mapped[List["Artifact"]] = relationship("Artifact", back_populates="job", cascade="all, delete-orphan")


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    symbols: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON or comma-separated symbols
    dependency_level: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[ChunkStatus] = mapped_column(
        Enum(ChunkStatus), default=ChunkStatus.PENDING, nullable=False
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    review_reason: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    lease_owner: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    job: Mapped["Job"] = relationship("Job", back_populates="chunks")
    attempts: Mapped[List["Attempt"]] = relationship("Attempt", back_populates="chunk", cascade="all, delete-orphan")
    agent_calls: Mapped[List["AgentCall"]] = relationship("AgentCall", back_populates="chunk", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_chunks_status_level", "status", "dependency_level"),
        Index("ix_chunks_job_status", "job_id", "status"),
    )


class Attempt(Base):
    __tablename__ = "attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    chunk_id: Mapped[str] = mapped_column(String(36), ForeignKey("chunks.id", ondelete="CASCADE"), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result: Mapped[str] = mapped_column(String(32), nullable=False)  # PASSED, FAILED, RETRY
    trimmed_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    chunk: Mapped["Chunk"] = relationship("Chunk", back_populates="attempts")

    __table_args__ = (
        Index("ix_attempts_chunk_attempt", "chunk_id", "attempt_number", unique=True),
    )


class AgentCall(Base):
    __tablename__ = "agent_calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    chunk_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("chunks.id", ondelete="SET NULL"), nullable=True)
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    tool_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    ok_flag: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped["Job"] = relationship("Job", back_populates="agent_calls")
    chunk: Mapped[Optional["Chunk"]] = relationship("Chunk", back_populates="agent_calls")

    __table_args__ = (
        Index("ix_agent_calls_job_created", "job_id", "created_at"),
    )


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)  # diff, report, golden_tests, etc.
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped["Job"] = relationship("Job", back_populates="artifacts")
