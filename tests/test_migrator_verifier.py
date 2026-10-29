"""Unit tests for Migrator and Verifier agents."""

import json
import unittest
from pathlib import Path

from lift.agents.migrator import MigratorAgent
from lift.agents.planner import ContextPack
from lift.agents.recorder import CharacterizationRecorder, GoldenExecution, GoldenSuite, TestCase
from lift.agents.verifier import VerifierAgent
from lift.llm.client import FakeLLMProvider, UnifiedLLMClient


class TestMigratorVerifier(unittest.TestCase):
    def setUp(self):
        self.workspace_root = Path(__file__).resolve().parent.parent
        self.benchmark_dir = self.workspace_root / "benchmarks" / "py2_app"
        self.verifier = VerifierAgent()

    def test_verifier_fails_on_syntax_error(self):
        """Requirement: Verifier must detect and reject syntax/compilation errors."""
        broken_code = "def invalid_func(\n    missing closing paren"
        res = self.verifier.verify(
            repo_root=str(self.workspace_root),
            chunk_file_path="benchmarks/py2_app/math_stats.py",
            candidate_code=broken_code,
        )
        self.assertFalse(res.passed)
        self.assertFalse(res.syntax_valid)
        self.assertEqual(res.triage_category, "syntax")
        self.assertIn("SyntaxError", res.trimmed_report)

    def test_verifier_fails_on_deliberately_broken_semantic_code(self):
        """Requirement: Verifier must correctly fail a deliberately broken semantic output."""
        # Golden suite expects integer division to produce 2 for [1, 2, 4]
        golden_suite = GoldenSuite(
            module_path="benchmarks/py2_app/math_stats.py",
            records=[
                GoldenExecution(
                    test_id="test_avg",
                    function_name="integer_average",
                    args=[[1, 2, 4]],
                    kwargs={},
                    return_value=2,  # Expected integer 2
                )
            ],
        )

        # Flawed Python 3 code with true division producing 2.3333333333333335
        flawed_code = """
def integer_average(numbers):
    if not numbers:
        return 0
    return sum(numbers) / len(numbers)
"""
        res = self.verifier.verify(
            repo_root=str(self.workspace_root),
            chunk_file_path="benchmarks/py2_app/math_stats.py",
            candidate_code=flawed_code,
            golden_suite=golden_suite,
        )

        self.assertFalse(res.passed)
        self.assertEqual(res.triage_category, "semantic")
        self.assertIn("test_avg", res.failing_tests)
        self.assertIn("Return value mismatch", res.trimmed_report)

    def test_verifier_passes_on_correctly_migrated_code(self):
        """Requirement: Verifier must pass code that preserves exact behavioral equivalence."""
        golden_suite = GoldenSuite(
            module_path="benchmarks/py2_app/math_stats.py",
            records=[
                GoldenExecution(
                    test_id="test_avg",
                    function_name="integer_average",
                    args=[[1, 2, 4]],
                    kwargs={},
                    return_value=2,
                )
            ],
        )

        # Correctly migrated Python 3 code using integer floor division //
        correct_code = """
def integer_average(numbers):
    if not numbers:
        return 0
    return sum(numbers) // len(numbers)
"""
        res = self.verifier.verify(
            repo_root=str(self.workspace_root),
            chunk_file_path="benchmarks/py2_app/math_stats.py",
            candidate_code=correct_code,
            golden_suite=golden_suite,
        )

        self.assertTrue(res.passed)
        self.assertEqual(len(res.failing_tests), 0)
        self.assertIn("zero regressions", res.trimmed_report)

    def test_migrator_end_to_end_attempt(self):
        """Requirement: Migrator agent produces candidate code with token and cost tracking."""
        candidate_payload = json.dumps({
            "migrated_code": "def integer_average(numbers):\n    return sum(numbers) // len(numbers)\n",
            "changes_explained": ["Used // floor division to preserve integer truncation"],
            "flagged_for_review": False,
            "review_note": None,
        })

        fake_provider = FakeLLMProvider(canned_responses=[candidate_payload])
        llm_client = UnifiedLLMClient(provider=fake_provider)
        migrator = MigratorAgent(llm_client=llm_client)

        context_pack = ContextPack(
            chunk_id="chunk_math_stats",
            file_path="benchmarks/py2_app/math_stats.py",
            dependency_level=0,
            source_code="def integer_average(numbers): return sum(numbers) / len(numbers)",
            recipe_hints=["Use // for integer division"],
        )

        attempt = migrator.migrate_chunk(
            context_pack=context_pack,
            rules_pass_code="def integer_average(numbers): return sum(numbers) / len(numbers)",
        )

        self.assertIn("//", attempt.candidate_code)
        self.assertEqual(len(attempt.changes_explained), 1)
        self.assertGreater(attempt.tokens_used, 0)


if __name__ == "__main__":
    unittest.main()
