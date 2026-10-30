"""Concurrent background worker queue with row-level locking, leases, and reapers."""

import threading
from datetime import datetime, timedelta, timezone
from typing import List, Optional
import uuid

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from lift.db.models import Chunk, ChunkStatus, ReviewReason, utcnow

_sqlite_lock = threading.Lock()


class ChunkLeaseManager:
    """Manages atomic row-level chunk claims, leases, heartbeats, and reapers."""

    @classmethod
    def claim_next_ready_chunk(
        cls,
        session: Session,
        worker_id: str,
        job_id: Optional[str] = None,
        lease_duration_seconds: int = 30,
    ) -> Optional[Chunk]:
        """Atomically claim the next unblocked PENDING chunk using row locking."""
        dialect_name = session.bind.dialect.name if session.bind else ""
        if dialect_name == "sqlite":
            with _sqlite_lock:
                return cls._claim_chunk_impl(session, worker_id, job_id, lease_duration_seconds)
        return cls._claim_chunk_impl(session, worker_id, job_id, lease_duration_seconds)

    @classmethod
    def _claim_chunk_impl(
        cls,
        session: Session,
        worker_id: str,
        job_id: Optional[str],
        lease_duration_seconds: int,
    ) -> Optional[Chunk]:
        dialect_name = session.bind.dialect.name if session.bind else ""
        query = select(Chunk).where(Chunk.status == ChunkStatus.PENDING)
        if job_id:
            query = query.where(Chunk.job_id == job_id)
        query = query.order_by(Chunk.dependency_level.asc(), Chunk.created_at.asc())

        if dialect_name == "postgresql":
            query = query.with_for_update(skip_locked=True)

        candidate_chunks = session.scalars(query).all()
        now = utcnow()
        lease_expiry = now + timedelta(seconds=lease_duration_seconds)

        for candidate in candidate_chunks:
            if not cls._are_dependencies_satisfied(session, candidate):
                continue

            stmt = (
                update(Chunk)
                .where(Chunk.id == candidate.id, Chunk.status == ChunkStatus.PENDING)
                .values(
                    status=ChunkStatus.MIGRATING,
                    lease_owner=worker_id,
                    lease_expires_at=lease_expiry,
                    updated_at=now,
                )
            )
            res = session.execute(stmt)
            session.flush()

            if res.rowcount > 0:
                session.refresh(candidate)
                return candidate

        return None

    @classmethod
    def heartbeat(
        cls,
        session: Session,
        chunk_id: str,
        worker_id: str,
        extension_seconds: int = 30,
    ) -> bool:
        """Extend active lease while worker is running."""
        now = utcnow()
        new_expiry = now + timedelta(seconds=extension_seconds)
        stmt = (
            update(Chunk)
            .where(
                Chunk.id == chunk_id,
                Chunk.lease_owner == worker_id,
                Chunk.status == ChunkStatus.MIGRATING,
            )
            .values(lease_expires_at=new_expiry, updated_at=now)
        )
        res = session.execute(stmt)
        session.flush()
        return res.rowcount > 0

    @classmethod
    def release_chunk(
        cls,
        session: Session,
        chunk_id: str,
        worker_id: str,
        final_status: ChunkStatus,
        review_reason: Optional[str] = None,
    ) -> bool:
        """Release lease and set final status."""
        dialect_name = session.bind.dialect.name if session.bind else ""
        if dialect_name == "sqlite":
            with _sqlite_lock:
                return cls._release_chunk_impl(session, chunk_id, worker_id, final_status, review_reason)
        return cls._release_chunk_impl(session, chunk_id, worker_id, final_status, review_reason)

    @classmethod
    def _release_chunk_impl(
        cls,
        session: Session,
        chunk_id: str,
        worker_id: str,
        final_status: ChunkStatus,
        review_reason: Optional[str] = None,
    ) -> bool:
        stmt = (
            update(Chunk)
            .where(Chunk.id == chunk_id, Chunk.lease_owner == worker_id)
            .values(
                status=final_status,
                review_reason=review_reason,
                lease_owner=None,
                lease_expires_at=None,
                updated_at=utcnow(),
            )
        )
        res = session.execute(stmt)
        session.flush()
        return res.rowcount > 0


    @classmethod
    def reap_expired_leases(cls, session: Session) -> int:
        """Reaper: Put abandoned chunks whose lease expired back to PENDING.
        
        Guarantees that a killed worker does not permanently hang the job.
        """
        now = utcnow()
        stmt = (
            update(Chunk)
            .where(
                Chunk.status == ChunkStatus.MIGRATING,
                Chunk.lease_expires_at < now,
            )
            .values(
                status=ChunkStatus.PENDING,
                lease_owner=None,
                lease_expires_at=None,
                review_reason=None,
                updated_at=now,
            )
        )
        res = session.execute(stmt)
        session.flush()
        return res.rowcount

    @classmethod
    def _are_dependencies_satisfied(cls, session: Session, chunk: Chunk) -> bool:
        """Verify that all lower dependency levels in the same job have PASSED."""
        dep_lvl = int(chunk.dependency_level or 0)
        if dep_lvl == 0:
            return True

        # Check for any unpassed chunks at a lower dependency level
        unpassed_query = select(Chunk).where(
            Chunk.job_id == str(chunk.job_id),
            Chunk.dependency_level < dep_lvl,
            Chunk.status != ChunkStatus.PASSED,
        )
        unpassed = session.scalars(unpassed_query).first()
        return unpassed is None

