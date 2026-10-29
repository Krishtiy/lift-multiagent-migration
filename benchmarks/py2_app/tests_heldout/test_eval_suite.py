# -*- coding: utf-8 -*-
"""Held-out evaluation test suite.

This suite is NEVER exposed to the migration agents. It serves as the
evaluation oracle in the experiment harness to test whether true semantic
equivalence was preserved or whether subtle regressions were introduced.
"""

import unittest
from benchmarks.py2_app.text_utils import safe_decode
from benchmarks.py2_app.math_stats import integer_average, sort_records_mixed, custom_compare_sort
from benchmarks.py2_app.data_store import InventoryStatus, ProductCatalog


class TestHeldOutOracle(unittest.TestCase):
    def test_integer_division_edge_cases(self):
        # 10 / 4 = 2 in Python 2 (2.5 in Python 3 true division)
        self.assertEqual(integer_average([10, 0]), 5)
        self.assertEqual(integer_average([5, 6]), 5)  # 11 / 2 = 5
        self.assertEqual(integer_average([-7, 2]), -3)  # -5 / 2 = -3 (floor division)

    def test_mixed_type_ordering(self):
        # Python 2 mixed-type ordering guarantees None < Numbers < Strings
        data = ["banana", 42, None, 10, "apple"]
        sorted_data = sort_records_mixed(data)
        self.assertEqual(sorted_data[0], None)
        self.assertEqual(sorted_data[1:3], [10, 42])
        self.assertEqual(sorted_data[3:], ["apple", "banana"])

    def test_custom_cmp_sorting(self):
        nums = [3, 1, 4, 1, 5, 9, 2]
        sorted_desc = custom_compare_sort(nums)
        self.assertEqual(sorted_desc, [9, 5, 4, 3, 2, 1, 1])

    def test_inventory_truthiness(self):
        # In Python 2, __nonzero__ controls bool()
        empty_stock = InventoryStatus(0)
        valid_stock = InventoryStatus(10)
        self.assertFalse(bool(empty_stock))
        self.assertTrue(bool(valid_stock))

    def test_safe_decode_bytes_and_unicode(self):
        u_str = u"héllo"
        raw_b = "world"
        self.assertEqual(safe_decode(u_str), u"héllo")
        self.assertEqual(safe_decode(raw_b), u"world")


if __name__ == "__main__":
    unittest.main()
