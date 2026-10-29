"""Unit tests for deterministic Python 2 to Python 3 rules pass."""

import ast
import unittest
from pathlib import Path

from lift.recipes.py2_to_py3.rules import Py2ToPy3RulesPass


class TestPy2ToPy3RulesPass(unittest.TestCase):
    def setUp(self):
        self.workspace_root = Path(__file__).resolve().parent.parent
        self.benchmark_dir = self.workspace_root / "benchmarks" / "py2_app"

    def test_print_transformation(self):
        snippet = 'def log():\n    print "User id:", uid\n    print "Done"'
        res = Py2ToPy3RulesPass.apply_rules(snippet)
        self.assertTrue(res.changed)
        self.assertIn('print("User id:", uid)', res.code)
        self.assertIn('print("Done")', res.code)
        self.assertTrue(any("G-01:print_function" in r for r in res.rules_applied))

    def test_except_as_transformation(self):
        snippet = 'try:\n    run()\nexcept (ValueError, TypeError), err:\n    handle(err)'
        res = Py2ToPy3RulesPass.apply_rules(snippet)
        self.assertTrue(res.changed)
        self.assertIn('except (ValueError, TypeError) as err:', res.code)
        self.assertTrue(any("G-02:except_as_syntax" in r for r in res.rules_applied))

    def test_xrange_to_range(self):
        snippet = 'for i in xrange(100):\n    calc(i)'
        res = Py2ToPy3RulesPass.apply_rules(snippet)
        self.assertTrue(res.changed)
        self.assertIn('for i in range(100):', res.code)
        self.assertTrue(any("G-03:xrange_to_range" in r for r in res.rules_applied))

    def test_has_key_and_iteritems(self):
        snippet = 'if d.has_key("k"):\n    for k, v in d.iteritems():\n        pass'
        res = Py2ToPy3RulesPass.apply_rules(snippet)
        self.assertTrue(res.changed)
        self.assertIn('"k" in d', res.code)
        self.assertIn('for k, v in d.items():', res.code)

    def test_rules_pass_on_benchmark_text_utils(self):
        """Verify rules pass allows text_utils.py to parse under Python 3 ast."""
        source = (self.benchmark_dir / "text_utils.py").read_text(encoding="utf-8")

        # In Python 3, raw text_utils.py triggers SyntaxError due to print and except ,
        with self.assertRaises(SyntaxError):
            ast.parse(source)

        res = Py2ToPy3RulesPass.apply_rules(source)
        self.assertTrue(res.changed)
        self.assertGreaterEqual(len(res.rules_applied), 3)

        # After deterministic rules pass, the file parses cleanly in Python 3!
        parsed_tree = ast.parse(res.code)
        self.assertIsNotNone(parsed_tree)

    def test_rules_pass_on_benchmark_data_store(self):
        """Verify rules pass modernizes has_key and iteritems in data_store.py."""
        source = (self.benchmark_dir / "data_store.py").read_text(encoding="utf-8")
        res = Py2ToPy3RulesPass.apply_rules(source)
        self.assertTrue(res.changed)
        self.assertIn("sku in self._items", res.code)
        self.assertIn("for sku, data in self._items.items():", res.code)


if __name__ == "__main__":
    unittest.main()
