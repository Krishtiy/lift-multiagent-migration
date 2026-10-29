"""Unit tests for deterministic repository analyzer and dependency graph builder."""

import tempfile
import unittest
from pathlib import Path

from lift.agents.analyzer import PythonSourceParser, RepositoryAnalyzer


class TestRepositoryAnalyzer(unittest.TestCase):
    def setUp(self):
        self.workspace_root = Path(__file__).resolve().parent.parent
        self.benchmark_dir = self.workspace_root / "benchmarks" / "py2_app"

    def test_parse_python2_source_with_fallback(self):
        """Verify that Python 2 syntax with print statement and comma except is parsed correctly."""
        py2_code = '''
def calculate_metric(val):
    print "Metric is:", val
    try:
        return val / 2
    except TypeError, e:
        return 0

class MetricReport(object):
    pass
'''
        symbols = PythonSourceParser.parse_source(py2_code, "metric.py")
        self.assertIn("calculate_metric", symbols.functions)
        self.assertIn("MetricReport", symbols.classes)

    def test_analyze_benchmark_py2_app(self):
        """Verify that benchmark app produces exact expected dependency graph and topological levels."""
        analyzer = RepositoryAnalyzer(str(self.benchmark_dir))
        graph = analyzer.analyze()

        # Find nodes
        nodes = {data["file_path"]: data for _, data in graph.nodes(data=True)}

        # Verify leaf nodes (Level 0)
        self.assertIn("text_utils.py", nodes)
        self.assertEqual(nodes["text_utils.py"]["dependency_level"], 0)
        self.assertIn("render_banner", nodes["text_utils.py"]["functions"])

        self.assertIn("math_stats.py", nodes)
        self.assertEqual(nodes["math_stats.py"]["dependency_level"], 0)
        self.assertIn("integer_average", nodes["math_stats.py"]["functions"])

        self.assertIn("data_store.py", nodes)
        self.assertEqual(nodes["data_store.py"]["dependency_level"], 0)
        self.assertIn("ProductCatalog", nodes["data_store.py"]["classes"])

        # Verify dependent node (Level 1)
        self.assertIn("report_gen.py", nodes)
        self.assertEqual(nodes["report_gen.py"]["dependency_level"], 1)
        self.assertIn("text_utils.py", nodes["report_gen.py"]["internal_dependencies"])
        self.assertIn("math_stats.py", nodes["report_gen.py"]["internal_dependencies"])

        # Verify serialized JSON format
        json_data = RepositoryAnalyzer.to_json_dict(graph)
        self.assertIn("nodes", json_data)
        self.assertIn("edges", json_data)
        self.assertGreaterEqual(len(json_data["nodes"]), 4)

        # Confirm Level 0 files appear before Level 1 in ordered output
        level_0_indices = [
            i for i, n in enumerate(json_data["nodes"]) if n["dependency_level"] == 0
        ]
        level_1_indices = [
            i for i, n in enumerate(json_data["nodes"]) if n["dependency_level"] == 1
        ]
        self.assertTrue(max(level_0_indices) < min(level_1_indices))

    def test_cycle_detection(self):
        """Verify analyzer flags cycles in circular dependencies."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            (tmp_path / "mod_a.py").write_text("import mod_b\ndef fn_a(): pass")
            (tmp_path / "mod_b.py").write_text("import mod_a\ndef fn_b(): pass")

            analyzer = RepositoryAnalyzer(str(tmp_path))
            graph = analyzer.analyze()

            self.assertTrue(graph.nodes["mod_a.py"].get("has_cycle", False))
            self.assertTrue(graph.nodes["mod_b.py"].get("has_cycle", False))


if __name__ == "__main__":
    unittest.main()
