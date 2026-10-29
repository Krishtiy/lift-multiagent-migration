"""Deterministic state machine and atomic chunk transition engine."""

from typing import Dict, Optional, Set, Union
from sqlalchemy import update
from sqlalchemy.orm import Session

from lift.db.models import Chunk, ChunkStatus, ReviewReason, utcnow


class InvalidTransitionError(Exception):
    """Raised when a state transition violates the state machine lifecycle."""
    pass


# Strict lifecycle definition for migration chunks
VALID_CHUNK_TRANSITIONS: Dict[ChunkStatus, Set[ChunkStatus]] = {
    ChunkStatus.PENDING: {
        ChunkStatus.TESTS_READY,
        ChunkStatus.NEEDS_REVIEW,
        ChunkStatus.FAILED,
    },
    ChunkStatus.TESTS_READY: {
        ChunkStatus.RULES_APPLIED,
        ChunkStatus.NEEDS_REVIEW,
        ChunkStatus.FAILED,
    },
    ChunkStatus.RULES_APPLIED: {
        ChunkStatus.MIGRATING,
        ChunkStatus.NEEDS_REVIEW,
        ChunkStatus.FAILED,
    },
    ChunkStatus.MIGRATING: {
        ChunkStatus.VERIFYING,
        ChunkStatus.NEEDS_REVIEW,
        ChunkStatus.FAILED,
    },
    ChunkStatus.VERIFYING: {
        ChunkStatus.PASSED,
        ChunkStatus.RETRY,
        ChunkStatus.NEEDS_REVIEW,
        ChunkStatus.FAILED,
    },
    ChunkStatus.RETRY: {
        ChunkStatus.MIGRATING,
        ChunkStatus.NEEDS_REVIEW,
        ChunkStatus.FAILED,
    },
    ChunkStatus.NEEDS_REVIEW: {
        ChunkStatus.PENDING,  # Re-queued upon human review/approval
    },
    ChunkStatus.PASSED: set(),  # Terminal success
    ChunkStatus.FAILED: {
        ChunkStatus.PENDING,  # Can be re-queued after resolving infra errors
    },
}


def can_transition(from_status: ChunkStatus, to_status: ChunkStatus) -> bool:
    """Check whether a transition from from_status to to_status is valid."""
    allowed = VALID_CHUNK_TRANSITIONS.get(from_status, set())
    return to_status in allowed


def transition_chunk(
    session: Session,
    chunk_id: str,
    from_status: ChunkStatus,
    to_status: ChunkStatus,
    review_reason: Optional[Union[ReviewReason, str]] = None,
    enforce_lifecycle: bool = True,
) -> bool:
    """Atomically transition a chunk from from_status to to_status.

    Uses conditional UPDATE:
      UPDATE chunks SET status = to_status ... WHERE id = chunk_id AND status = from_status
    
    Returns:
      True if the status was successfully changed.
      False if chunk was already moved by another worker or not in from_status.

    Raises:
      InvalidTransitionError if enforce_lifecycle is True and the transition is illegal.
    """
    if enforce_lifecycle and not can_transition(from_status, to_status):
        raise InvalidTransitionError(
            f"Illegal chunk transition from {from_status.value} to {to_status.value}."
        )

    reason_str = (
        review_reason.value if isinstance(review_reason, ReviewReason) else review_reason
    )

    update_values = {
        "status": to_status,
        "updated_at": utcnow(),
    }
    if reason_str is not None:
        update_values["review_reason"] = reason_str

    stmt = (
        update(Chunk)
        .where(Chunk.id == chunk_id, Chunk.status == from_status)
        .values(**update_values)
    )

    result = session.execute(stmt)
    session.flush()

    return result.rowcount > 0
