"""Unit tests for the Lift state machine and atomic transitions."""

import unittest
from concurrent.futures import ThreadPoolExecutor

from lift.core.state_machine import (
    InvalidTransitionError,
    can_transition,
    transition_chunk,
)
from lift.db.models import Chunk, ChunkStatus, Job, JobStatus, ReviewReason
from lift.db.session import create_db_engine, get_db, init_db


class TestStateMachine(unittest.TestCase):
    def setUp(self):
        # Use an in-memory SQLite database for fast, isolated testing
        self.engine = create_db_engine("sqlite:///:memory:")
        init_db(self.engine)

        # Seed a test job and chunk
        with get_db(self.engine) as session:
            job = Job(
                id="job-test-01",
                recipe="py2_to_py3",
                repo_reference="benchmarks/py2_app",
                status=JobStatus.RUNNING,
            )
            session.add(job)
            chunk = Chunk(
                id="chunk-test-01",
                job_id="job-test-01",
                file_path="benchmarks/py2_app/math_stats.py",
                dependency_level=0,
                status=ChunkStatus.PENDING,
            )
            session.add(chunk)

    def test_valid_lifecycle_transitions(self):
        """Verify normal progression: PENDING -> TESTS_READY -> RULES_APPLIED -> MIGRATING -> VERIFYING -> PASSED."""
        with get_db(self.engine) as session:
            # 1. PENDING -> TESTS_READY
            ok = transition_chunk(
                session, "chunk-test-01", ChunkStatus.PENDING, ChunkStatus.TESTS_READY
            )
            self.assertTrue(ok)

            # 2. TESTS_READY -> RULES_APPLIED
            ok = transition_chunk(
                session, "chunk-test-01", ChunkStatus.TESTS_READY, ChunkStatus.RULES_APPLIED
            )
            self.assertTrue(ok)

            # 3. RULES_APPLIED -> MIGRATING
            ok = transition_chunk(
                session, "chunk-test-01", ChunkStatus.RULES_APPLIED, ChunkStatus.MIGRATING
            )
            self.assertTrue(ok)

            # 4. MIGRATING -> VERIFYING
            ok = transition_chunk(
                session, "chunk-test-01", ChunkStatus.MIGRATING, ChunkStatus.VERIFYING
            )
            self.assertTrue(ok)

            # 5. VERIFYING -> PASSED
            ok = transition_chunk(
                session, "chunk-test-01", ChunkStatus.VERIFYING, ChunkStatus.PASSED
            )
            self.assertTrue(ok)

            # Verify in DB
            chunk = session.get(Chunk, "chunk-test-01")
            self.assertEqual(chunk.status, ChunkStatus.PASSED)

    def test_invalid_transition_rejected(self):
        """Verify skipping states (e.g. PENDING -> PASSED) raises an error."""
        self.assertFalse(can_transition(ChunkStatus.PENDING, ChunkStatus.PASSED))

        with get_db(self.engine) as session:
            with self.assertRaises(InvalidTransitionError):
                transition_chunk(
                    session, "chunk-test-01", ChunkStatus.PENDING, ChunkStatus.PASSED
                )

    def test_needs_review_records_reason(self):
        """Verify transition to NEEDS_REVIEW records the exact failure reason."""
        with get_db(self.engine) as session:
            transition_chunk(
                session,
                "chunk-test-01",
                ChunkStatus.PENDING,
                ChunkStatus.NEEDS_REVIEW,
                review_reason=ReviewReason.MAX_ATTEMPTS,
            )
            chunk = session.get(Chunk, "chunk-test-01")
            self.assertEqual(chunk.status, ChunkStatus.NEEDS_REVIEW)
            self.assertEqual(chunk.review_reason, "max_attempts")

    def test_concurrent_transitions_exactly_one_succeeds(self):
        """Simulate two workers attempting to claim/transition the same chunk concurrently."""
        results = []

        def attempt_claim(worker_id):
            with get_db(self.engine) as session:
                success = transition_chunk(
                    session,
                    "chunk-test-01",
                    ChunkStatus.PENDING,
                    ChunkStatus.TESTS_READY,
                )
                return success

        # Run concurrent attempts using a thread pool
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(attempt_claim, i) for i in range(2)]
            results = [f.result() for f in futures]

        # Exactly one worker must succeed, the other must fail (False)
        self.assertEqual(sorted(results), [False, True])


if __name__ == "__main__":
    unittest.main()
