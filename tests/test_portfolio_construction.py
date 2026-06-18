import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rhbot.panel import Panel
from rhbot.portfolio_construction import (
    apply_no_trade_band, blend, construct, turnover)
from rhbot.strategies import CompositeStrategy
from rhbot.strategies.base import Strategy, TargetBasket
from rhbot.strategy_config import StrategyConfig


class _FakeSleeve(Strategy):
    def __init__(self, name, weights):
        self.name = name
        self._w = weights

    def required_history(self):
        return 5

    def generate(self, panel, members, asof, cfg, denylist=None, sectors=None):
        b = TargetBasket(asof=asof, strategy=self.name)
        b.weights = dict(self._w)
        b.selected = list(self._w)
        b.n_considered, b.n_dq_pass, b.n_valid = 100, 95, len(self._w)
        return b


def mkpanel(series):
    n = max(len(v) for v in series.values())
    dates = [f"2026-01-{i + 1:02d}" for i in range(n)]
    prices = {s: {dates[i]: p for i, p in enumerate(px)} for s, px in series.items()}
    return Panel(prices, dates)


class ConstructTest(unittest.TestCase):
    def test_name_cap_leaves_cash(self):
        cfg = StrategyConfig(max_name_weight=0.12, weight_scheme="equal")
        sel = ["A", "B", "C", "D", "E"]
        panel = mkpanel({s: [10, 10] for s in sel})
        w, _ = construct(sel, 5, panel, "2026-01-02", cfg, sectors=None)
        for s in sel:
            self.assertAlmostEqual(w[s], 0.12, places=6)
        self.assertAlmostEqual(sum(w.values()), 0.60, places=6)   # 0.40 cash

    def test_sector_cap_bounds_each_sector(self):
        cfg = StrategyConfig(max_name_weight=0.50, max_sector_weight=0.30,
                             weight_scheme="equal")
        sel = ["A", "B", "C", "D"]
        sectors = {"A": "Tech", "B": "Tech", "C": "Tech", "D": "Fin"}
        panel = mkpanel({s: [10, 10] for s in sel})
        w, notes = construct(sel, 4, panel, "2026-01-02", cfg, sectors=sectors)
        self.assertLessEqual(w["A"] + w["B"] + w["C"], 0.30 + 1e-6)
        self.assertLessEqual(w["D"], 0.30 + 1e-6)
        self.assertAlmostEqual(sum(w.values()), 0.60, places=4)    # 2 sectors x 30%
        self.assertTrue(any("top sector" in n for n in notes))

    def test_inverse_vol_tilts_to_low_vol(self):
        cfg = StrategyConfig(weight_scheme="inverse_vol", vol_lookback_days=8,
                             max_name_weight=0.90)
        steady = [100, 101, 100, 101, 100, 101, 100, 101, 100]
        wild = [100, 120, 90, 125, 85, 130, 80, 135, 75]
        panel = mkpanel({"A": steady, "B": wild})
        w, _ = construct(["A", "B"], 2, panel, panel.latest_date(), cfg, sectors=None)
        self.assertGreater(w["A"], w["B"])                         # low-vol gets more
        self.assertAlmostEqual(sum(w.values()), 1.0, places=6)

    def test_vol_target_scales_down_exposure(self):
        cfg = StrategyConfig(weight_scheme="equal", vol_lookback_days=8,
                             target_annual_vol=0.10, max_name_weight=0.90)
        wild = [100, 130, 80, 140, 70, 150, 60, 160, 50]           # very high vol
        panel = mkpanel({"A": wild, "B": wild})
        w, notes = construct(["A", "B"], 2, panel, panel.latest_date(), cfg, sectors=None)
        self.assertLess(sum(w.values()), 1.0)                      # held partly in cash
        self.assertTrue(any("vol-target" in n for n in notes))


    def test_turnover(self):
        self.assertAlmostEqual(turnover({"A": 0.5, "B": 0.5}, {"A": 0.5, "C": 0.5}), 0.5)
        self.assertAlmostEqual(turnover({"A": 1.0}, {"A": 1.0}), 0.0)

    def test_no_trade_band_holds_small_deltas(self):
        banded = apply_no_trade_band({"A": 0.52, "B": 0.20}, {"A": 0.50, "B": 0.50}, band=0.05)
        self.assertAlmostEqual(banded["A"], 0.50)        # 0.02 <= band -> hold
        self.assertAlmostEqual(banded["B"], 0.20)        # 0.30  > band -> trade
        self.assertEqual(apply_no_trade_band({"A": 1.0}, {"A": 1.0}, 0.0), {"A": 1.0})

    def test_blend_mixes_and_renormalizes(self):
        b = blend([{"A": 1.0}, {"B": 1.0}], [0.5, 0.5])
        self.assertAlmostEqual(b["A"], 0.5)
        self.assertAlmostEqual(b["B"], 0.5)
        b2 = blend([{"A": 1.0}, {"B": 1.0}], [3, 1])     # unequal -> renormalized
        self.assertAlmostEqual(b2["A"], 0.75)
        self.assertAlmostEqual(b2["B"], 0.25)

    def test_composite_blends_sleeves(self):
        cfg = StrategyConfig(max_name_weight=0.5, max_sector_weight=1.0)
        comp = CompositeStrategy([(_FakeSleeve("a", {"AAA": 1.0}), 0.5),
                                  (_FakeSleeve("b", {"BBB": 1.0}), 0.5)])
        b = comp.generate(None, set(), "2026-01-01", cfg, sectors=None)
        self.assertAlmostEqual(b.weights["AAA"], 0.5)
        self.assertAlmostEqual(b.weights["BBB"], 0.5)
        self.assertEqual(b.n_dq_pass, 95)                # feed-health: max over sleeves
        self.assertEqual(comp.required_history(), 5)


if __name__ == "__main__":
    unittest.main()
