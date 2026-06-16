import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rhbot.strategies.base import TargetBasket
from rhbot.portfolio_risk import vet
from rhbot.strategy_config import StrategyConfig


def basket(weights, considered, dq_pass, valid=None):
    sel = list(weights)
    return TargetBasket(asof="2026-06-16", strategy="t", weights=dict(weights),
                        selected=sel, n_considered=considered,
                        n_dq_pass=dq_pass, n_valid=valid if valid is not None else len(sel))


class PortfolioRiskTest(unittest.TestCase):
    def setUp(self):
        self.cfg = StrategyConfig()

    def test_healthy_basket_trades(self):
        w = {f"S{i}": 0.1 for i in range(10)}
        d = vet(basket(w, considered=12, dq_pass=11), self.cfg)
        self.assertEqual(d.action, "TRADE")
        self.assertAlmostEqual(d.diagnostics["gross_exposure"], 1.0, places=5)
        self.assertEqual(d.blockers, [])

    def test_low_data_quality_blocks(self):
        w = {f"S{i}": 0.1 for i in range(10)}
        d = vet(basket(w, considered=20, dq_pass=10), self.cfg)   # 50% < 80%
        self.assertEqual(d.action, "DO_NOT_TRADE")
        self.assertTrue(any("data-feed" in b for b in d.blockers))
        self.assertEqual(d.final_weights, {})

    def test_too_few_names_blocks(self):
        w = {"A": 0.1, "B": 0.1, "C": 0.1}
        d = vet(basket(w, considered=12, dq_pass=12), self.cfg)
        self.assertEqual(d.action, "DO_NOT_TRADE")
        self.assertTrue(any("too concentrated" in b for b in d.blockers))

    def test_per_name_cap_applied(self):
        w = {"A": 0.2, "B": 0.1, "C": 0.1, "D": 0.1, "E": 0.1}
        d = vet(basket(w, considered=5, dq_pass=5), self.cfg)
        self.assertEqual(d.action, "TRADE")
        self.assertAlmostEqual(d.final_weights["A"], self.cfg.max_name_weight, places=5)
        self.assertTrue(any("capped" in wmsg for wmsg in d.warnings))

    def test_scale_warning_on_small_account(self):
        w = {f"S{i}": 0.02 for i in range(50)}   # 50 names
        d = vet(basket(w, considered=50, dq_pass=50), self.cfg, account_value=30.0)
        self.assertEqual(d.action, "TRADE")           # tradeable, but...
        self.assertTrue(any("below the" in wmsg for wmsg in d.warnings))

    def test_renormalize_option(self):
        cfg = StrategyConfig(on_reject="renormalize", max_name_weight=1.0)
        w = {f"S{i}": 0.1 for i in range(5)}           # gross 0.5
        d = vet(basket(w, considered=6, dq_pass=6), cfg)
        self.assertAlmostEqual(sum(d.final_weights.values()), 1.0, places=5)


if __name__ == "__main__":
    unittest.main()
