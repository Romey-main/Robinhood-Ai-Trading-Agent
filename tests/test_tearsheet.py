import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rhbot.tearsheet import drawdown_series


class TearsheetTest(unittest.TestCase):
    def test_drawdown_series(self):
        eq = [("d0", 1.0), ("d1", 1.2), ("d2", 0.9), ("d3", 1.1)]
        dd = drawdown_series(eq)
        self.assertAlmostEqual(dd[0][1], 0.0)
        self.assertAlmostEqual(dd[1][1], 0.0)            # new peak
        self.assertAlmostEqual(dd[2][1], 0.9 / 1.2 - 1)  # -25% off the 1.2 peak
        self.assertAlmostEqual(dd[3][1], 1.1 / 1.2 - 1)  # still underwater vs 1.2

    def test_monotonic_rise_has_no_drawdown(self):
        dd = drawdown_series([("d0", 1.0), ("d1", 1.1), ("d2", 1.3)])
        self.assertTrue(all(abs(v) < 1e-12 for _, v in dd))


if __name__ == "__main__":
    unittest.main()
