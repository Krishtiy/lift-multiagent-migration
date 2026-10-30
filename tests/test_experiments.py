"""Unit tests for Phase 13 Evaluation Harness and Experiments."""

import os
from pathlib import Path
import tempfile
import unittest

from experiments.harness import (
    ConditionConfig,
    ExperimentConfig,
    ExperimentHarness,
    GotchaAuditor,
    RunMetric,
)


class TestExperimentHarness(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_config_loading_all_yamls(self):
        config_files = [
            "experiments/configs/e0_baselines.yaml",
            "experiments/configs/e1_ablation_rules.yaml",
            "experiments/configs/e2_retries.yaml",
            "experiments/configs/e3_roles.yaml",
            "experiments/configs/e4_models.yaml",
        ]
        for cfg_path in config_files:
            self.assertTrue(os.path.exists(cfg_path), f"Missing config: {cfg_path}")
            cfg = ExperimentConfig.from_yaml(cfg_path)
            self.assertTrue(len(cfg.conditions) >= 2)
            self.assertTrue(cfg.runs >= 1)
            for cond in cfg.conditions:
                self.assertIsInstance(cond.name, str)
                self.assertIsInstance(cond.apply_rules, bool)
                self.assertIsInstance(cond.max_attempts, int)

    def test_gotcha_auditor_baseline(self):
        audit = GotchaAuditor.audit_repo("benchmarks/py2_app")
        # In un-migrated benchmark repo, planted gotchas should be present (i.e. not resolved)
        self.assertIn("G-01", audit)
        self.assertIn("G-02", audit)
        self.assertIn("G-03", audit)
        self.assertIn("G-04", audit)
        self.assertIn("G-05", audit)
        self.assertIn("G-07", audit)
        self.assertIn("G-08", audit)

        # In unmigrated code, print statements, xrange, iteritems, has_key are not resolved
        self.assertFalse(audit["G-01"])
        self.assertFalse(audit["G-03"])
        self.assertFalse(audit["G-04"])
        self.assertFalse(audit["G-07"])
        self.assertFalse(audit["G-08"])

    def test_experiment_harness_execution(self):
        harness = ExperimentHarness(
            benchmark_repo="benchmarks/py2_app",
            fake_llm=True,
            cache_dir=os.path.join(self.temp_dir.name, "cache"),
        )
        test_config = ExperimentConfig(
            name="Test Experiment",
            description="Unit test run",
            runs=1,
            conditions=[
                ConditionConfig(
                    name="test_rules_only",
                    apply_rules=True,
                    max_attempts=0,
                    skip_verifier=True,
                ),
            ],
        )

        metrics = harness.run_experiment(test_config, runs_override=1)
        self.assertEqual(len(metrics), 1)
        m = metrics[0]
        self.assertEqual(m.condition_name, "test_rules_only")
        self.assertEqual(m.run_index, 1)
        self.assertGreater(m.total_chunks, 0)
        self.assertEqual(m.total_gotchas, 12)

        # Test aggregation
        aggregates = harness.aggregate_metrics(metrics)
        self.assertEqual(len(aggregates), 1)
        agg = aggregates[0]
        self.assertEqual(agg.condition_name, "test_rules_only")
        self.assertEqual(agg.total_runs, 1)

        # Test markdown rendering
        md = harness.render_markdown_table(aggregates)
        self.assertIn("| **test_rules_only** |", md)
        self.assertIn("First-Try Pass %", md)

        # Test CSV export
        csv_out = os.path.join(self.temp_dir.name, "results.csv")
        harness.export_csv(metrics, csv_out)
        self.assertTrue(os.path.exists(csv_out))
        content = Path(csv_out).read_text(encoding="utf-8")
        self.assertIn("condition_name", content)
        self.assertIn("test_rules_only", content)


if __name__ == "__main__":
    unittest.main()
