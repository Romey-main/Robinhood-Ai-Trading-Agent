import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rhbot.paper_engine import PaperLedger


class PaperEngineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        self.path = self.tmp.name

    def tearDown(self):
        os.unlink(self.path)

    def _ledger(self):
        return PaperLedger(self.path)

    def test_target_hit_closes_as_win(self):
        led = self._ledger()
        led.open_trade("AMD", qty=5, entry_price=100, stop_price=92,
                       target_price=112, entry_time="2026-06-16T14:00:00+00:00")
        closed = led.mark("AMD", last=113, high=113, low=101)
        self.assertEqual(len(closed), 1)
        t = led.closed_trades[0]
        self.assertEqual(t.exit_reason, "target")
        self.assertAlmostEqual(t.exit_price, 112)
        self.assertGreater(t.pnl(), 0)

    def test_stop_hit_closes_as_loss(self):
        led = self._ledger()
        led.open_trade("SOFI", qty=5, entry_price=10, stop_price=9.2,
                       target_price=11.2, entry_time="2026-06-16T14:00:00+00:00")
        led.mark("SOFI", last=9.0, high=10.1, low=9.0)
        t = led.closed_trades[0]
        self.assertEqual(t.exit_reason, "stop")
        self.assertLess(t.pnl(), 0)

    def test_stop_wins_when_bar_touches_both(self):
        # conservative: if a single bar spans stop AND target, assume stop
        led = self._ledger()
        led.open_trade("X", qty=1, entry_price=100, stop_price=92,
                       target_price=112, entry_time="2026-06-16T14:00:00+00:00")
        led.mark("X", last=105, high=120, low=90)
        self.assertEqual(led.closed_trades[0].exit_reason, "stop")

    def test_stats_winrate_and_expectancy(self):
        led = self._ledger()
        # two winners (+12%), one loser (-8%) -> 66.7% win rate
        for i, (entry, stop, tgt, last) in enumerate([
            (100, 92, 112, 113),
            (50, 46, 56, 57),
            (20, 18.4, 22.4, 18.0),
        ]):
            led.open_trade(f"S{i}", qty=1, entry_price=entry, stop_price=stop,
                           target_price=tgt,
                           entry_time=f"2026-06-16T1{i}:00:00+00:00")
        led.mark("S0", last=113, high=113, low=101)
        led.mark("S1", last=57, high=57, low=51)
        led.mark("S2", last=18.0, high=20.1, low=18.0)

        s = led.stats()
        self.assertEqual(s["n_closed"], 3)
        self.assertEqual(s["n_wins"], 2)
        self.assertEqual(s["n_losses"], 1)
        self.assertAlmostEqual(s["win_rate"], 2 / 3, places=3)
        self.assertAlmostEqual(s["avg_win_pct"], 0.12, places=3)
        self.assertAlmostEqual(s["avg_loss_pct"], -0.08, places=3)
        # expectancy = 2/3*0.12 + 1/3*(-0.08) = 0.0533...
        self.assertAlmostEqual(s["expectancy_pct"], 0.0533, places=3)

    def test_persistence_roundtrip(self):
        led = self._ledger()
        led.open_trade("AMD", qty=2, entry_price=100, stop_price=92,
                       target_price=112, entry_time="2026-06-16T14:00:00+00:00")
        led.save()
        reloaded = self._ledger()
        self.assertEqual(len(reloaded.open_trades), 1)
        self.assertEqual(reloaded.open_trades[0].symbol, "AMD")


if __name__ == "__main__":
    unittest.main()
