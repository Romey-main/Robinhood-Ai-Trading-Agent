import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import synth
from rhbot.strategy_config import StrategyConfig
from rhbot.strategies import ShortTermReversal, Momentum12_1
from rhbot.universe import Membership
from rhbot.backtest import (run_backtest, weekly_rebalance_dates,
                            monthly_rebalance_dates)


class RebalanceDatesTest(unittest.TestCase):
    def test_weekly_one_per_iso_week(self):
        from datetime import date
        dates = synth.business_dates(30)
        wk = weekly_rebalance_dates(synth.panel_from_paths({"X": [1.0] * 30}, dates))
        weeks = {date.fromisoformat(d).isocalendar()[:2] for d in dates}
        self.assertEqual(len(wk), len(weeks))

    def test_monthly_one_per_month(self):
        dates = synth.business_dates(70)
        mo = monthly_rebalance_dates(synth.panel_from_paths({"X": [1.0] * 70}, dates))
        months = {d[:7] for d in dates}
        self.assertEqual(len(mo), len(months))


class MomentumBacktestTest(unittest.TestCase):
    def test_trending_market_is_profitable(self):
        dates = synth.business_dates(520)
        drifts = {f"S{i}": 0.0003 * (i - 2) for i in range(8)}
        paths = {s: synth.geometric(520, 100, g) for s, g in drifts.items()}
        panel = synth.panel_from_paths(paths, dates)
        cfg = StrategyConfig(min_price=10.0)
        membership = Membership(None, set(paths))     # static universe for the test
        mdates = monthly_rebalance_dates(panel)
        res = run_backtest(Momentum12_1(top_n=5), panel, membership, cfg,
                           mdates, periods_per_year=12.0)
        self.assertIn("sharpe", res.stats)
        self.assertGreater(res.n_traded, 0)
        self.assertGreater(res.stats["total_return"], 0.0)
        self.assertEqual(res.n_traded + res.n_cash, res.n_periods)


class MeanReversionBacktestMechanicsTest(unittest.TestCase):
    def test_runs_and_accounts_every_period(self):
        dates = synth.business_dates(60)
        paths = {f"S{i}": synth.geometric(60, 50 + i, 0.0005 * (i - 3))
                 for i in range(6)}
        panel = synth.panel_from_paths(paths, dates)
        cfg = StrategyConfig(min_price=10.0)
        membership = Membership(None, set(paths))
        wk = weekly_rebalance_dates(panel)
        res = run_backtest(ShortTermReversal(lookback_days=5, top_n=10), panel,
                           membership, cfg, wk, periods_per_year=52.0)
        self.assertEqual(len(res.equity), res.n_periods + 1)
        self.assertEqual(res.equity[0][1], 1.0)
        self.assertEqual(res.n_traded + res.n_cash, res.n_periods)
        self.assertIn("max_drawdown", res.stats)


if __name__ == "__main__":
    unittest.main()
