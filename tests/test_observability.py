"""Unit tests for Phase 12 Observability, analytics calculations, and live HTML dashboard."""

import unittest
from fastapi.testclient import TestClient

from lift.api.dashboard import compute_job_analytics, render_dashboard_html
from lift.api.routes import create_app
from lift.db.models import AgentCall, Chunk, ChunkStatus, Job, JobStatus
from lift.db.session import create_db_engine, drop_db, get_db, init_db


class TestObservability(unittest.TestCase):
    def setUp(self):
        self.engine = create_db_engine("sqlite:///:memory:")
        init_db(self.engine)
        self.app = create_app(engine=self.engine)
        self.client = TestClient(self.app)

        self.job_id = "job_obs_test"
        # Seed test data: job, 2 chunks, multiple agent calls with different costs, latencies, and failure flags
        with get_db(self.engine) as session:
            job = Job(
                id=self.job_id,
                recipe="py2_to_py3",
                repo_reference="benchmarks/py2_app",
                status=JobStatus.RUNNING,
                token_budget=500_000,
                tokens_used=12_500,
                cost_usd=0.0152,
            )
            session.add(job)

            chunk1 = Chunk(
                id="chunk_text_utils",
                job_id=self.job_id,
                file_path="benchmarks/py2_app/text_utils.py",
                dependency_level=0,
                status=ChunkStatus.PASSED,
                attempt_count=1,
            )
            chunk2 = Chunk(
                id="chunk_math_stats",
                job_id=self.job_id,
                file_path="benchmarks/py2_app/math_stats.py",
                dependency_level=0,
                status=ChunkStatus.NEEDS_REVIEW,
                attempt_count=3,
                review_reason="max_attempts",
            )
            session.add_all([chunk1, chunk2])

            # Call 1 on chunk 1: Analyzer (fast, ok)
            call1 = AgentCall(
                job_id=self.job_id,
                chunk_id=chunk1.id,
                agent_name="Analyzer",
                model="mock-model",
                prompt_hash="h1",
                tokens_in=500,
                tokens_out=100,
                cost_usd=0.001,
                latency_ms=120,
                tool_name="ast_parser",
                ok_flag=True,
            )
            # Call 2 on chunk 2: Migrator (expensive, high tokens, failed)
            call2 = AgentCall(
                job_id=self.job_id,
                chunk_id=chunk2.id,
                agent_name="Migrator",
                model="mock-model",
                prompt_hash="h2",
                tokens_in=8000,
                tokens_out=1500,
                cost_usd=0.012,
                latency_ms=2500,  # Slowest
                tool_name="rewrite_chunk",
                ok_flag=False,  # Failed attempt
            )
            # Call 3 on chunk 2: Verifier (cheap, ok)
            call3 = AgentCall(
                job_id=self.job_id,
                chunk_id=chunk2.id,
                agent_name="Verifier",
                model="mock-model",
                prompt_hash="h3",
                tokens_in=1000,
                tokens_out=200,
                cost_usd=0.0022,
                latency_ms=450,
                tool_name="sandbox_runner",
                ok_flag=True,
            )
            session.add_all([call1, call2, call3])

    def tearDown(self):
        drop_db(self.engine)

    def test_analytics_metrics_calculation(self):
        """Requirement: For any job, answer which chunk cost most, slowest stage, and which agent fails most."""
        with get_db(self.engine) as session:
            analytics = compute_job_analytics(session, self.job_id)

            # 1. Which chunk cost the most? -> chunk_math_stats ($0.0142 total)
            self.assertEqual(
                analytics["most_expensive_chunk"]["chunk_id"], "chunk_math_stats"
            )
            self.assertEqual(
                analytics["most_expensive_chunk"]["file_path"],
                "benchmarks/py2_app/math_stats.py",
            )
            self.assertGreater(analytics["most_expensive_chunk"]["cost_usd"], 0.01)

            # 2. Which stage/operation was slowest? -> Migrator (2500 ms)
            self.assertEqual(analytics["slowest_stage"]["agent_name"], "Migrator")
            self.assertEqual(analytics["slowest_stage"]["latency_ms"], 2500)

            # 3. Which agent fails the most? -> Migrator (1 failure)
            self.assertEqual(
                analytics["agent_failure_summary"]["most_failing_agent"], "Migrator"
            )

    def test_analytics_api_endpoint(self):
        """Verify GET /jobs/{job_id}/analytics returns JSON analytics payload."""
        res = self.client.get(f"/jobs/{self.job_id}/analytics")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["job_id"], self.job_id)
        self.assertEqual(
            data["most_expensive_chunk"]["chunk_id"], "chunk_math_stats"
        )
        self.assertEqual(data["slowest_stage"]["agent_name"], "Migrator")

    def test_dashboard_html_rendering(self):
        """Verify GET /dashboard/{job_id} returns responsive HTML dashboard with chunk grid."""
        res = self.client.get(f"/dashboard/{self.job_id}")
        self.assertEqual(res.status_code, 200)
        self.assertIn("text/html", res.headers.get("content-type", ""))

        html = res.text
        # Must contain title, chunk paths, status indicators, and KPI metrics
        self.assertIn("Lift Migration Observability", html)
        self.assertIn("benchmarks/py2_app/text_utils.py", html)
        self.assertIn("benchmarks/py2_app/math_stats.py", html)
        self.assertIn("PASSED", html)
        self.assertIn("NEEDS_REVIEW", html)
        self.assertIn("Migrator", html)
        self.assertIn("Retry", html)
        self.assertIn("Approve", html)


if __name__ == "__main__":
    unittest.main()
