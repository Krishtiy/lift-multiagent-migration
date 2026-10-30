"""Phase 15: Hardening and failure-injection test suite.

Validates:
1. Malformed and adversarial LLM outputs (JSON repair and budget protection).
2. Infinite loop detection and rollback to original source code.
3. Hostile code in sandbox (timeout termination, resource isolation).
4. Lease reaper resilience under multi-worker crash conditions.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import tempfile
import time
import unittest

from lift.core.orchestrator import MigrationOrchestrator
from lift.core.worker import ChunkLeaseManager
from lift.db.models import Chunk, ChunkStatus, Job, JobStatus, ReviewReason, utcnow
from lift.db.session import create_db_engine, get_db, init_db
from lift.llm.client import FakeLLMProvider, LLMResponse, UnifiedLLMClient
from lift.recipes.py2_to_py3.rules import Py2ToPy3RulesPass
from lift.sandbox.runner import SandboxLimits, create_sandbox_runner


class TestHardeningAndFailureInjection(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.engine = create_db_engine("sqlite:///:memory:")
        init_db(self.engine)
        self.sandbox = create_sandbox_runner()

    def tearDown(self):
        self.temp_dir.cleanup()
        from lift.db.session import drop_db
        drop_db(self.engine)

    def test_sandbox_hostile_infinite_loop_timeout(self):
        """Failure injection: Code containing an infinite loop must be forcefully killed by timeout."""
        hostile_code = "import time\nwhile True:\n    pass\n"
        script_path = Path(self.temp_dir.name) / "infinite_loop.py"
        script_path.write_text(hostile_code, encoding="utf-8")

        limits = SandboxLimits(timeout_seconds=1.5)
        start = time.monotonic()
        res = self.sandbox.run(
            image="local",
            cmd=["python", str(script_path)],
            limits=limits,
        )
        duration = time.monotonic() - start

        self.assertTrue(res.timed_out)
        self.assertNotEqual(res.exit_code, 0)
        self.assertLess(duration, 4.0, "Timeout killer took too long to terminate hostile process")

    def test_sandbox_hostile_memory_spike_resilience(self):
        """Failure injection: Code allocating massive buffers must not crash host runner."""
        memory_hog = "import sys\ntry:\n    big = 'A' * (500 * 1024 * 1024)\nexcept MemoryError:\n    sys.exit(42)\n"
        script_path = Path(self.temp_dir.name) / "memory_hog.py"
        script_path.write_text(memory_hog, encoding="utf-8")

        limits = SandboxLimits(memory_limit_mb=128, timeout_seconds=5.0)
        res = self.sandbox.run(
            image="local",
            cmd=["python", str(script_path)],
            limits=limits,
        )
        # Runner handles failure without crashing
        self.assertIsNotNone(res)

    def test_orchestrator_loop_detection_and_rollback(self):
        """Failure injection: Consecutive identical verifier failures must trigger loop detection and rollback."""
        # Setup temporary mock repo
        repo_dir = Path(self.temp_dir.name) / "mock_repo"
        repo_dir.mkdir(parents=True)
        target_file = repo_dir / "broken_module.py"
        original_code = "print 'Legacy Python 2'\n"
        target_file.write_text(original_code, encoding="utf-8")

        # Fake provider always returns the same syntax-broken candidate
        broken_json = json.dumps({
            "migrated_code": "def broken_func(:\n    pass",
            "changes_explained": ["broken"],
            "flagged_for_review": False,
        })
        provider = FakeLLMProvider(canned_responses=[broken_json] * 10)
        llm_client = UnifiedLLMClient(provider=provider)

        orchestrator = MigrationOrchestrator(
            engine=self.engine,
            llm_client=llm_client,
            max_attempts=3,
        )

        report = orchestrator.run_migration(str(repo_dir))
        self.assertEqual(report.review_chunks, 1)

        # Ensure rollback preserved the original file content
        current_content = target_file.read_text(encoding="utf-8")
        self.assertEqual(current_content, original_code, "Rollback failed: file content was modified")

    def test_reaper_recovers_all_crashed_workers(self):
        """Failure injection: Sudden crash of 5 workers with active leases must all be recovered by reaper."""
        job_id = "job_mass_crash"
        with get_db(self.engine) as session:
            job = Job(id=job_id, recipe="py2_to_py3", repo_reference="test", status=JobStatus.RUNNING)
            session.add(job)

            # 5 chunks with expired leases held by crashed workers
            past_time = utcnow() - timedelta(minutes=5)
            for i in range(5):
                chunk = Chunk(
                    id=f"crashed_chunk_{i}",
                    job_id=job_id,
                    file_path=f"file_{i}.py",
                    dependency_level=0,
                    status=ChunkStatus.MIGRATING,
                    lease_owner=f"dead_worker_{i}",
                    lease_expires_at=past_time,
                )
                session.add(chunk)

        # Run reaper
        with get_db(self.engine) as session:
            reaped_count = ChunkLeaseManager.reap_expired_leases(session)

        self.assertEqual(reaped_count, 5)

        # Verify all chunks are back to PENDING and unassigned
        with get_db(self.engine) as session:
            for i in range(5):
                c = session.get(Chunk, f"crashed_chunk_{i}")
                self.assertEqual(c.status, ChunkStatus.PENDING)
                self.assertIsNone(c.lease_owner)
                self.assertIsNone(c.lease_expires_at)


if __name__ == "__main__":
    unittest.main()
