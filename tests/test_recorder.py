"""Unit tests for characterization test recorder, golden master generation, and comparator."""

import unittest
from pathlib import Path

from lift.agents.recorder import (
    CharacterizationRecorder,
    GoldenComparator,
    GoldenExecution,
    InputProposalGenerator,
    TestCase,
)


class TestCharacterizationRecorder(unittest.TestCase):
    def setUp(self):
        self.workspace_root = Path(__file__).resolve().parent.parent
        self.recorder = CharacterizationRecorder()

    def test_input_proposal_edge_cases(self):
        cases = InputProposalGenerator.propose_inputs_for_function("integer_average", ["numbers"])
        test_ids = [c.test_id for c in cases]
        self.assertIn("integer_average_empty", test_ids)
        self.assertIn("integer_average_integers", test_ids)
        self.assertIn("integer_average_negatives", test_ids)

    def test_record_and_verify_three_runs_repeatability(self):
        """Record behavior on legacy code and prove it runs repeatably 3 times in a row."""
        cases = [
            TestCase("case_empty", "integer_average", args=[[]]),
            TestCase("case_positives", "integer_average", args=[[10, 20, 30]]),
            TestCase("case_series", "generate_series", args=[5, 3, 2]),
        ]

        # Run 1: Create Golden Baseline
        golden_suite = self.recorder.record_module(
            repo_root=str(self.workspace_root),
            module_rel_path="benchmarks/py2_app/math_stats.py",
            test_cases=cases,
        )

        self.assertEqual(len(golden_suite.records), 3)
        rec_empty = next(r for r in golden_suite.records if r.test_id == "case_empty")
        self.assertEqual(rec_empty.return_value, 0)

        rec_series = next(r for r in golden_suite.records if r.test_id == "case_series")
        self.assertEqual(rec_series.return_value, [5, 7, 9])

        # Runs 2 and 3: Re-execute and verify against Golden Baseline
        for run_idx in (2, 3):
            subsequent_suite = self.recorder.record_module(
                repo_root=str(self.workspace_root),
                module_rel_path="benchmarks/py2_app/math_stats.py",
                test_cases=cases,
            )
            for golden_rec, actual_rec in zip(golden_suite.records, subsequent_suite.records):
                comp = GoldenComparator.compare(golden_rec, actual_rec)
                self.assertTrue(
                    comp.passed,
                    f"Run {run_idx} failed repeatability on {comp.test_id}: {comp.diff_reason}"
                )

    def test_comparator_detects_division_semantic_drift(self):
        """Verify comparator flags divergence when float division replaces integer division."""
        golden = GoldenExecution(
            test_id="test_avg",
            function_name="integer_average",
            args=[[1, 2, 4]],
            kwargs={},
            return_value=2,  # Python 2 integer division: 7 // 3 = 2
            stdout="",
        )

        # Naive Python 3 float division output
        actual_flawed = GoldenExecution(
            test_id="test_avg",
            function_name="integer_average",
            args=[[1, 2, 4]],
            kwargs={},
            return_value=2.3333333333333335,  # 7 / 3
            stdout="",
        )

        result = GoldenComparator.compare(golden, actual_flawed)
        self.assertFalse(result.passed)
        self.assertIn("Return value mismatch", result.diff_reason)

        # Correct migrated output with integer truncation // preserved
        actual_correct = GoldenExecution(
            test_id="test_avg",
            function_name="integer_average",
            args=[[1, 2, 4]],
            kwargs={},
            return_value=2,
            stdout="",
        )
        self.assertTrue(GoldenComparator.compare(golden, actual_correct).passed)


if __name__ == "__main__":
    unittest.main()
