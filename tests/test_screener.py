import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rhbot.config import Config
from rhbot.models import Snapshot
from rhbot.screener import screen, momentum_score, volatility_score


def snap(symbol, price=50, dvol=5e8, vol=0.05, mom=0.02, sent=0.0):
    return Snapshot(symbol=symbol, price=price, avg_dollar_volume=dvol,
                    volatility=vol, momentum=mom, sentiment=sent)


class ScreenerTest(unittest.TestCase):
    def setUp(self):
        self.cfg = Config()

    def test_penny_and_illiquid_are_filtered(self):
        snaps = [
            snap("PENNY", price=0.50),                 # below min_price
            snap("THIN", dvol=1e6),                    # below liquidity floor
            snap("QUIET", vol=0.005),                  # below vol floor
            snap("PUMP", vol=0.40),                    # above vol ceiling
            snap("GOOD"),                              # passes
        ]
        passers, rejected = screen(snaps, self.cfg, top=10)
        passed = {c.symbol for c in passers}
        rejected_syms = {c.symbol for c in rejected}
        self.assertEqual(passed, {"GOOD"})
        self.assertEqual(rejected_syms, {"PENNY", "THIN", "QUIET", "PUMP"})

    def test_ranking_prefers_better_sentiment_when_else_equal(self):
        snaps = [snap("LO", sent=-0.5), snap("HI", sent=0.8)]
        passers, _ = screen(snaps, self.cfg, top=10)
        self.assertEqual(passers[0].symbol, "HI")
        self.assertGreater(passers[0].score, passers[1].score)

    def test_ranking_is_deterministic(self):
        snaps = [snap("A", sent=0.1), snap("B", sent=0.4), snap("C", sent=-0.2)]
        first = [c.symbol for c in screen(snaps, self.cfg, top=10)[0]]
        second = [c.symbol for c in screen(snaps, self.cfg, top=10)[0]]
        self.assertEqual(first, second)
        self.assertEqual(first, ["B", "A", "C"])

    def test_momentum_penalizes_blowoff(self):
        cfg = self.cfg
        # a moderate move should outscore a parabolic one
        self.assertGreater(momentum_score(0.03, cfg), momentum_score(0.30, cfg))
        # negative momentum scores below neutral
        self.assertLess(momentum_score(-0.05, cfg), 0.5)

    def test_volatility_floor_and_ceiling(self):
        cfg = self.cfg
        self.assertEqual(volatility_score(0.01, cfg), 0.0)
        self.assertGreater(volatility_score(0.10, cfg), 0.0)


if __name__ == "__main__":
    unittest.main()
