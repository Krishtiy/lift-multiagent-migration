# -*- coding: utf-8 -*-
"""Development test suite visible to the migration pipeline."""

import unittest
from benchmarks.py2_app.text_utils import render_banner, hash_text, format_error_message
from benchmarks.py2_app.math_stats import integer_average, generate_series, parse_large_id
from benchmarks.py2_app.data_store import ProductCatalog, InventoryStatus


class TestDevSuite(unittest.TestCase):
    def test_text_utils_banner(self):
        banner = render_banner("Lift System")
        self.assertTrue("Lift System" in banner)
        self.assertTrue("=" in banner)

    def test_text_utils_hash(self):
        digest = hash_text("hello_world", salt="salt123")
        self.assertEqual(len(digest), 32)

    def test_text_utils_error_msg(self):
        msg = format_error_message("test failure")
        self.assertEqual(msg, "Captured: test failure")

    def test_math_integer_average(self):
        # 1 + 2 + 4 = 7; 7 / 3 in Python 2 is 2
        result = integer_average([1, 2, 4])
        self.assertEqual(result, 2)

    def test_math_generate_series(self):
        series = generate_series(start=10, count=4, step=5)
        self.assertEqual(series, [10, 15, 20, 25])

    def test_math_parse_large_id(self):
        val = parse_large_id("9876543210123456789")
        self.assertEqual(str(val), "9876543210123456789")

    def test_catalog_membership_and_filter(self):
        cat = ProductCatalog()
        cat.add_product("SKU-1", "Widget", 15.0, stock=5)
        cat.add_product("SKU-2", "Gizmo", 25.0, stock=0)

        self.assertTrue(cat.has_product("SKU-1"))
        self.assertFalse(cat.has_product("SKU-999"))
        self.assertEqual(cat.get_all_skus(), ["SKU-1", "SKU-2"])

        # SKU-2 has 0 stock -> bool(status) should be False
        active = cat.get_active_items()
        self.assertEqual(active, [("SKU-1", "Widget")])

        # Filter by max price 20 -> should match SKU-1
        cheapest = cat.filter_by_max_price(20.0)
        self.assertEqual(cheapest, "SKU-1")


if __name__ == "__main__":
    unittest.main()
