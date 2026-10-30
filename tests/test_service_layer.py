"""Unit tests for FastAPI endpoints, 4-worker queue concurrency, and lease reaper."""

import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from fastapi.testclient import TestClient

from lift.api.routes import create_app
from lift.core.worker import ChunkLeaseManager
from lift.db.models import Chunk, ChunkStatus, Job, JobStatus, utcnow
from lift.db.session import create_db_engine, get_db, init_db


class TestServiceLayer(unittest.TestCase):
    def setUp(self):
        self.engine = create_db_engine("sqlite:///:memory:")
        init_db(self.engine)
        self.app = create_app(engine=self.engine)
        self.client = TestClient(self.app)

    def tearDown(self):
        from lift.db.session import drop_db
        drop_db(self.engine)


    def test_api_job_lifecycle_endpoints(self):
        """Verify REST endpoints: POST /jobs, GET /jobs/{id}, retry and approve."""
        # 1. Create Job
        create_res = self.client.post(
            "/jobs",
            json={"recipe": "py2_to_py3", "repo_path": "benchmarks/py2_app", "token_budget": 300_000},
        )
        self.assertEqual(create_res.status_code, 201)
        job_data = create_res.json()
        job_id = job_data["id"]

        # Seed a test chunk
        with get_db(self.engine) as session:
            chunk = Chunk(
                id="chunk_review_test",
                job_id=job_id,
                file_path="benchmarks/py2_app/math_stats.py",
                dependency_level=0,
                status=ChunkStatus.NEEDS_REVIEW,
                review_reason="max_attempts",
            )
            session.add(chunk)

        # 2. GET /jobs/{id}
        status_res = self.client.get(f"/jobs/{job_id}")
        self.assertEqual(status_res.status_code, 200)
        self.assertEqual(status_res.json()["chunk_counts"].get("NEEDS_REVIEW"), 1)

        # 3. GET /jobs/{id}/chunks
        chunks_res = self.client.get(f"/jobs/{job_id}/chunks")
        self.assertEqual(chunks_res.status_code, 200)
        self.assertEqual(len(chunks_res.json()), 1)

        # 4. POST /jobs/{id}/chunks/{cid}/retry
        retry_res = self.client.post(f"/jobs/{job_id}/chunks/chunk_review_test/retry")
        self.assertEqual(retry_res.status_code, 200)
        self.assertEqual(retry_res.json()["new_status"], "PENDING")

        # 5. POST /jobs/{id}/chunks/{cid}/approve
        approve_res = self.client.post(f"/jobs/{job_id}/chunks/chunk_review_test/approve")
        self.assertEqual(approve_res.status_code, 200)
        self.assertEqual(approve_res.json()["new_status"], "PASSED")

    def test_four_workers_concurrent_claims_zero_duplicates(self):
        """Requirement: 4 workers claim ready chunks in parallel with zero duplicate processing."""
        job_id = "job_concurrency_test"
        with get_db(self.engine) as session:
            job = Job(id=job_id, recipe="py2_to_py3", repo_reference="test", status=JobStatus.RUNNING)
            session.add(job)

            # Create 8 Level 0 chunks ready for processing
            for i in range(8):
                c = Chunk(
                    id=f"chunk_c_{i}",
                    job_id=job_id,
                    file_path=f"file_{i}.py",
                    dependency_level=0,
                    status=ChunkStatus.PENDING,
                )
                session.add(c)

        claimed_chunks = []

        def worker_task(worker_idx):
            worker_id = f"worker_{worker_idx}"
            my_claims = []
            while True:
                # 1. Claim in its own committed transaction
                with get_db(self.engine) as session:
                    claimed = ChunkLeaseManager.claim_next_ready_chunk(
                        session=session, worker_id=worker_id, job_id=job_id
                    )
                    chunk_id = claimed.id if claimed else None

                if not chunk_id:
                    break

                my_claims.append(chunk_id)

                # 2. Release in its own committed transaction
                with get_db(self.engine) as session:
                    ChunkLeaseManager.release_chunk(
                        session=session,
                        chunk_id=chunk_id,
                        worker_id=worker_id,
                        final_status=ChunkStatus.PASSED,
                    )
            return my_claims

        # Run 4 workers concurrently in a thread pool
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(worker_task, i) for i in range(4)]
            for f in futures:
                claimed_chunks.extend(f.result())

        # Verify all 8 chunks were processed
        self.assertEqual(len(claimed_chunks), 8)
        # Verify ZERO duplicate claims (every chunk processed exactly once)
        self.assertEqual(len(set(claimed_chunks)), 8)


    def test_reaper_recovers_abandoned_lease(self):
        """Requirement: Killing a worker mid-chunk allows reaper to put chunk back to PENDING."""
        job_id = "job_reaper_test"
        with get_db(self.engine) as session:
            job = Job(id=job_id, recipe="py2_to_py3", repo_reference="test", status=JobStatus.RUNNING)
            session.add(job)

            # Chunk claimed by dead worker with expired lease
            expired_time = utcnow() - timedelta(minutes=5)
            c = Chunk(
                id="chunk_abandoned",
                job_id=job_id,
                file_path="abandoned.py",
                dependency_level=0,
                status=ChunkStatus.MIGRATING,
                lease_owner="worker_crashed",
                lease_expires_at=expired_time,
            )
            session.add(c)

        with get_db(self.engine) as session:
            # Run reaper
            reaped_count = ChunkLeaseManager.reap_expired_leases(session)
            self.assertEqual(reaped_count, 1)

            # Verify chunk is restored to PENDING and unassigned
            restored = session.get(Chunk, "chunk_abandoned")
            self.assertEqual(restored.status, ChunkStatus.PENDING)
            self.assertIsNone(restored.lease_owner)


if __name__ == "__main__":
    unittest.main()
