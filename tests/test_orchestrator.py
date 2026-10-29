"""Unit tests for the deterministic MigrationOrchestrator control loop (Milestone 1)."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from lift.core.orchestrator import MigrationOrchestrator
from lift.db.models import Chunk, ChunkStatus
from lift.db.session import create_db_engine, get_db
from lift.llm.client import FakeLLMProvider, UnifiedLLMClient


class TestMigrationOrchestrator(unittest.TestCase):
    def setUp(self):
        self.workspace_root = Path(__file__).resolve().parent.parent
        self.benchmark_dir = self.workspace_root / "benchmarks" / "py2_app"
        self.temp_cache = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_cache.cleanup()

    def test_retry_and_eventual_success(self):
        """Requirement: A fake LLM that fails twice then succeeds is retried correctly."""
        # Attempt 1: Valid JSON, but candidate code has Python syntax error
        resp1 = json.dumps({"migrated_code": "def broken_syntax():\n    return ((\n", "changes_explained": []})
        # Attempt 2: Valid JSON, but fails test equivalence (wrong return value)
        resp2 = json.dumps({"migrated_code": "def integer_average(numbers):\n    return -999\n", "changes_explained": []})
        # Attempt 3: Valid JSON, valid Python 3 syntax, passes all characterization tests
        resp3 = json.dumps({"migrated_code": "def integer_average(numbers):\n    if not numbers:\n        return 0\n    return sum(numbers) / len(numbers)\n", "changes_explained": []})

        from lift.llm.cache import LLMCache
        isolated_cache = LLMCache(cache_dir=self.temp_cache.name)
        fake_provider = FakeLLMProvider(canned_responses=[resp1, resp2, resp3])
        llm_client = UnifiedLLMClient(provider=fake_provider, cache=isolated_cache)


        engine = create_db_engine("sqlite:///:memory:")
        orchestrator = MigrationOrchestrator(engine=engine, llm_client=llm_client)

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_repo = Path(tmp_dir)
            target_file = tmp_repo / "math_stats.py"
            target_file.write_text("def integer_average(numbers):\n    if not numbers:\n        return 0\n    return sum(numbers) / len(numbers)\n", encoding="utf-8")

            report = orchestrator.run_migration(repo_path=str(tmp_repo), recipe="py2_to_py3")


            # Must have retried and passed on attempt 3
            summary = next(s for s in report.chunk_summaries if "math_stats" in s["file_path"])
            self.assertEqual(summary["status"], ChunkStatus.PASSED.value)
            self.assertEqual(summary["attempts"], 3)



    def test_persistent_failure_rolls_back_to_needs_review(self):
        """Requirement: A fake LLM that never succeeds ends in NEEDS_REVIEW with a rollback."""
        # 3 broken responses
        broken_resp = json.dumps({"migrated_code": "def invalid(\n  error", "changes_explained": []})
        fake_provider = FakeLLMProvider(canned_responses=[broken_resp, broken_resp, broken_resp])
        llm_client = UnifiedLLMClient(provider=fake_provider)

        engine = create_db_engine("sqlite:///:memory:")
        orchestrator = MigrationOrchestrator(engine=engine, llm_client=llm_client)

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_repo = Path(tmp_dir)
            target_file = tmp_repo / "math_stats.py"
            original_code = "def integer_average(numbers):\n    return sum(numbers) / len(numbers)\n"
            target_file.write_text(original_code, encoding="utf-8")

            report = orchestrator.run_migration(repo_path=str(tmp_repo), recipe="py2_to_py3")

            summary = next(s for s in report.chunk_summaries if "math_stats" in s["file_path"])
            self.assertEqual(summary["status"], ChunkStatus.NEEDS_REVIEW.value)
            # Prove rollback: File must be restored to original code
            self.assertEqual(target_file.read_text(encoding="utf-8"), original_code)

    def test_stuck_loop_detection(self):
        """Requirement: Detecting identical failure twice consecutively stops immediately as stuck."""
        same_failing_resp = json.dumps({
            "migrated_code": "def integer_average(numbers):\n    return sum(numbers) / len(numbers)\n",
            "changes_explained": [],
        })
        fake_provider = FakeLLMProvider(canned_responses=[same_failing_resp, same_failing_resp])
        llm_client = UnifiedLLMClient(provider=fake_provider)

        engine = create_db_engine("sqlite:///:memory:")
        orchestrator = MigrationOrchestrator(engine=engine, llm_client=llm_client)

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_repo = Path(tmp_dir)
            target_file = tmp_repo / "math_stats.py"
            original_code = "def integer_average(numbers):\n    return sum(numbers) / len(numbers)\n"
            target_file.write_text(original_code, encoding="utf-8")

            report = orchestrator.run_migration(repo_path=str(tmp_repo), recipe="py2_to_py3")

            summary = next(s for s in report.chunk_summaries if "math_stats" in s["file_path"])
            self.assertEqual(summary["status"], ChunkStatus.NEEDS_REVIEW.value)
            self.assertEqual(summary["reason"], "stuck")
            self.assertEqual(summary["attempts"], 2)  # Stopped at attempt 2 due to loop detection


if __name__ == "__main__":
    unittest.main()
