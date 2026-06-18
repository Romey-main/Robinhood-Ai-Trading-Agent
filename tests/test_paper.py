import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rhbot.panel import Panel
from rhbot.paper import PaperLedger, mark


class PaperTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        # A: 100 -> 110 (+10%) -> 121 (+10%)
        self.panel = Panel(
            {"A": {"2026-01-02": 100.0, "2026-02-02": 110.0, "2026-03-02": 121.0}},
            ["2026-01-02", "2026-02-02", "2026-03-02"])

    def test_record_roundtrip(self):
        p = os.path.join(self.dir, "led.json")
        PaperLedger(p).record("2026-01-02", "momentum", {"A": 1.0})
        reloaded = PaperLedger(p)
        self.assertEqual(len(reloaded.entries), 1)
        self.assertEqual(reloaded.entries[0]["strategy"], "momentum")
        self.assertEqual(reloaded.entries[0]["weights"], {"A": 1.0})

    def test_mark_chains_returns(self):
        entries = [{"date": "2026-01-02", "strategy": "s", "weights": {"A": 1.0}},
                   {"date": "2026-02-02", "strategy": "s", "weights": {"A": 1.0}}]
        rows, eq, stats = mark(entries, self.panel, "2026-03-02")
        self.assertAlmostEqual(rows[0]["ret"], 0.10, places=6)
        self.assertAlmostEqual(rows[1]["ret"], 0.10, places=6)   # open leg, marked to asof
        self.assertAlmostEqual(eq[-1][1], 1.21, places=4)        # 1.1 * 1.1
        self.assertEqual(stats["missing_forward_prices"], 0)

    def test_single_open_entry_has_no_marked_period(self):
        rows, eq, stats = mark(
            [{"date": "2026-03-02", "strategy": "s", "weights": {"A": 1.0}}],
            self.panel, "2026-03-02")
        self.assertIn("note", stats)
        self.assertTrue(rows[0]["open"])
        self.assertIsNone(rows[0]["ret"])


if __name__ == "__main__":
    unittest.main()
