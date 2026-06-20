import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import synth
from rhbot.strategy_config import StrategyConfig
from rhbot.strategies import ShortTermReversal, Momentum12_1
from rhbot.universe import Denylist


class MeanReversionTest(unittest.TestCase):
    def setUp(self):
        self.cfg = StrategyConfig(min_price=10.0, event_move_cap=0.20)
        self.dates = synth.business_dates(10)
        self.asof = self.dates[-1]
        # 5-day trailing returns engineered via flat-then-ramp (idx4 -> idx9)
        def path(t, start=100.0):
            return synth.ramp(10, start, start * (1 + t), flat_until=4)
        self.paths = {
            "AAA": path(-0.10),   # worst
            "BBB": path(-0.06),
            "CCC": path(-0.03),
            "DDD": path(+0.02),
            "EEE": path(+0.08),
            "EVT": path(-0.30),   # event filter: |30%| > 20%
            "LOW": path(-0.15, start=5.0),   # below $10 floor
            "DENY": path(-0.12),  # denylisted
        }

    def test_buys_the_worst_performers(self):
        panel = synth.panel_from_paths(self.paths, self.dates)
        strat = ShortTermReversal(lookback_days=5, top_n=3)
        b = strat.generate(panel, set(self.paths), self.asof, self.cfg,
                           denylist=Denylist({"DENY"}))
        self.assertEqual(b.selected, ["AAA", "BBB", "CCC"])
        # equal-weighted, but each capped at the per-name limit (rest -> cash):
        # 3 names would be 1/3 each, which exceeds the 12% concentration cap.
        expect = min(1 / 3, self.cfg.max_name_weight)
        for s in b.selected:
            self.assertAlmostEqual(b.weights[s], expect, places=5)

    def test_event_floor_deny_excluded(self):
        panel = synth.panel_from_paths(self.paths, self.dates)
        strat = ShortTermReversal(lookback_days=5, top_n=10)
        b = strat.generate(panel, set(self.paths), self.asof, self.cfg,
                           denylist=Denylist({"DENY"}))
        self.assertIn("EVT", b.rejects)
        self.assertIn("LOW", b.rejects)
        self.assertIn("DENY", b.rejects)
        self.assertNotIn("EVT", b.selected)

    def test_fewer_than_topn_holds_cash(self):
        panel = synth.panel_from_paths(self.paths, self.dates)
        strat = ShortTermReversal(lookback_days=5, top_n=10)
        b = strat.generate(panel, set(self.paths), self.asof, self.cfg,
                           denylist=Denylist({"DENY"}))
        # 5 valid names, nominal 10% each -> 50% invested, 50% cash
        self.assertEqual(len(b.selected), 5)
        self.assertAlmostEqual(b.gross, 0.5, places=5)
        self.assertAlmostEqual(b.cash_weight, 0.5, places=5)


class MomentumTest(unittest.TestCase):
    def setUp(self):
        self.cfg = StrategyConfig(min_price=10.0)
        self.dates = synth.business_dates(300)
        self.asof = self.dates[-1]
        # increasing drift by index -> higher formation return
        self.drifts = {f"S{i}": 0.0002 * (i - 2) for i in range(8)}  # some negative
        self.paths = {s: synth.geometric(300, 100, g) for s, g in self.drifts.items()}

    def test_buys_top_formation_returns(self):
        panel = synth.panel_from_paths(self.paths, self.dates)
        strat = Momentum12_1(top_n=5)
        b = strat.generate(panel, set(self.paths), self.asof, self.cfg)
        self.assertEqual(len(b.selected), 5)
        # highest-drift name ranks first; lowest two never selected
        self.assertEqual(b.selected[0], "S7")
        self.assertNotIn("S0", b.selected)
        self.assertNotIn("S1", b.selected)

    def test_denylist_excluded(self):
        panel = synth.panel_from_paths(self.paths, self.dates)
        strat = Momentum12_1(top_n=5)
        b = strat.generate(panel, set(self.paths), self.asof, self.cfg,
                           denylist=Denylist({"S7"}))
        self.assertNotIn("S7", b.selected)
        self.assertIn("S7", b.rejects)


if __name__ == "__main__":
    unittest.main()
