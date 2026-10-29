import unittest
import lift


class TestSmoke(unittest.TestCase):
    def test_lift_version(self):
        self.assertEqual(lift.__version__, "0.1.0")


def test_lift_version_pytest():
    assert lift.__version__ == "0.1.0"

