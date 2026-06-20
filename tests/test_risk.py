import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rhbot.config import Config
from rhbot.models import Snapshot
from rhbot.risk import position_size, stop_and_target, evaluate_trade


def snap(symbol="AMD", price=10.0):
    return Snapshot(symbol=symbol, price=price, avg_dollar_volume=5e8,
                    volatility=0.05, momentum=0.02, sentiment=0.3)


class RiskTest(unittest.TestCase):
    def setUp(self):
        self.cfg = Config()  # $50 bp, 95% pos, 8% stop, 12% target

    def test_position_size_respects_budget(self):
        qty = position_size(50.0, 10.0, self.cfg)
        self.assertLessEqual(qty * 10.0, 50.0 * 0.95 + 1e-9)
        self.assertAlmostEqual(qty, 4.75, places=6)

    def test_stop_and_target_levels(self):
        stop, target = stop_and_target(100.0, self.cfg)
        self.assertAlmostEqual(stop, 92.0, places=2)
        self.assertAlmostEqual(target, 112.0, places=2)

    def test_approves_reasonable_trade(self):
        d = evaluate_trade(snap(price=10.0), self.cfg, open_positions=0)
        self.assertTrue(d.approved, d.reasons)
        self.assertGreater(d.qty, 0)
        self.assertGreater(d.reward_risk, self.cfg.min_reward_risk)

    def test_rejects_when_already_holding(self):
        d = evaluate_trade(snap(), self.cfg, open_positions=1)
        self.assertFalse(d.approved)

    def test_rejects_price_below_min(self):
        d = evaluate_trade(snap(price=1.0), self.cfg, open_positions=0)
        self.assertFalse(d.approved)

    def test_reward_risk_matches_config_ratio(self):
        # 12% target / 8% stop == 1.5 reward:risk regardless of price
        d = evaluate_trade(snap(price=20.0), self.cfg, open_positions=0)
        self.assertAlmostEqual(d.reward_risk, 1.5, places=1)


if __name__ == "__main__":
    unittest.main()
