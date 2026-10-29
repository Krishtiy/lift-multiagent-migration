"""Unit tests for deterministic planner, chunker, and context pack generation."""

import unittest
from pathlib import Path

from lift.agents.analyzer import RepositoryAnalyzer
from lift.agents.planner import RepositoryPlanner, SignatureExtractor


class TestRepositoryPlanner(unittest.TestCase):
    def setUp(self):
        self.workspace_root = Path(__file__).resolve().parent.parent
        self.benchmark_dir = self.workspace_root / "benchmarks" / "py2_app"

    def test_signature_extraction(self):
        sample_code = """
def calculate(a, b=10):
    # Long implementation details here
    x = a * b
    return x

class Calculator(object):
    def add(self, x, y):
        return x + y
"""
        sigs = SignatureExtractor.extract_from_source(sample_code)
        self.assertTrue(any("def calculate" in s for s in sigs))
        self.assertTrue(any("class Calculator" in s for s in sigs))
        # Ensure implementation bodies are excluded
        self.assertFalse(any("x = a * b" in s for s in sigs))

    def test_plan_benchmark_app(self):
        """Verify benchmark app produces ordered chunks, signatures, and parallel tags."""
        analyzer = RepositoryAnalyzer(str(self.benchmark_dir))
        graph = analyzer.analyze()

        planner = RepositoryPlanner(
            repo_root=str(self.benchmark_dir),
            token_budget_per_chunk=4000,
            recipe_name="py2_to_py3",
        )
        plan = planner.plan_from_graph(graph)

        # 1. Total chunks must be at least 4 (text_utils, math_stats, data_store, report_gen)
        self.assertGreaterEqual(plan.total_chunks, 4)

        # 2. Check Level 0 chunks appear before Level 1 chunks
        level_0_chunks = plan.chunks_by_level[0]
        level_1_chunks = plan.chunks_by_level[1]

        level_0_files = {c.file_path for c in level_0_chunks}
        self.assertIn("text_utils.py", level_0_files)
        self.assertIn("math_stats.py", level_0_files)
        self.assertIn("data_store.py", level_0_files)

        level_1_files = {c.file_path for c in level_1_chunks}
        self.assertIn("report_gen.py", level_1_files)

        # 3. Parallel safety: Level 0 has 3 independent chunks -> must be marked parallel-safe
        for chunk in level_0_chunks:
            self.assertTrue(chunk.is_parallel_safe)

        # 4. Context pack for report_gen must contain signatures of its dependencies
        report_chunk = next(c for c in plan.chunks if c.file_path == "report_gen.py")
        self.assertIn("text_utils.py", report_chunk.dependency_signatures)
        self.assertIn("math_stats.py", report_chunk.dependency_signatures)

        # Verify signatures inside report_chunk
        text_sigs = report_chunk.dependency_signatures["text_utils.py"]
        self.assertTrue(any("render_banner" in s for s in text_sigs))

        # 5. Token budget adherence
        for chunk in plan.chunks:
            self.assertLess(chunk.estimated_tokens, 4000)
            self.assertGreater(chunk.estimated_tokens, 0)
            self.assertGreaterEqual(len(chunk.recipe_hints), 5)


if __name__ == "__main__":
    unittest.main()
