import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import synth
from rhbot.strategy_config import StrategyConfig
from rhbot.data_quality import validate_series
from rhbot.universe import Denylist


class DataQualityTest(unittest.TestCase):
    def setUp(self):
        self.cfg = StrategyConfig(min_price=10.0)
        self.dates = synth.business_dates(12)
        self.asof = self.dates[-1]

    def _panel(self, prices):
        return synth.panel_from_paths({"GOOD": prices}, self.dates)

    def _check(self, panel, sym="GOOD", req=7, deny=None):
        return validate_series(panel, sym, self.asof, self.cfg,
                               required_days=req, denylist=deny)

    def test_clean_series_passes(self):
        panel = self._panel(synth.geometric(12, 100, 0.002))
        self.assertTrue(self._check(panel).ok)

    def test_denylisted_rejected(self):
        panel = self._panel(synth.geometric(12, 100, 0.002))
        chk = self._check(panel, deny=Denylist({"GOOD"}))
        self.assertFalse(chk.ok)
        self.assertIn("denylisted", chk.reject_reasons[0])

    def test_missing_symbol_rejected(self):
        panel = self._panel(synth.geometric(12, 100, 0.002))
        self.assertFalse(self._check(panel, sym="NOPE").ok)

    def test_insufficient_history_rejected(self):
        panel = synth.panel_from_paths(
            {"GOOD": synth.geometric(5, 100, 0.002)}, synth.business_dates(5))
        chk = validate_series(panel, "GOOD", panel.latest_date(), self.cfg,
                              required_days=7)
        self.assertFalse(chk.ok)
        self.assertTrue(any("insufficient_history" in r for r in chk.reject_reasons))

    def test_nonfinite_price_rejected(self):
        p = synth.geometric(12, 100, 0.002)
        p[6] = float("nan")
        self.assertFalse(self._check(self._panel(p)).ok)

    def test_below_price_floor_rejected(self):
        panel = self._panel(synth.geometric(12, 5, 0.002))   # ~$5 < $10 floor
        chk = self._check(panel)
        self.assertFalse(chk.ok)
        self.assertTrue(any("below_price_floor" in r for r in chk.reject_reasons))

    def test_stale_feed_rejected(self):
        panel = self._panel(synth.geometric(12, 100, 0.002))
        # evaluate 10 days after the last available price
        from datetime import date, timedelta
        far = (date.fromisoformat(self.dates[-1]) + timedelta(days=10)).isoformat()
        chk = validate_series(panel, "GOOD", far, self.cfg, required_days=7)
        self.assertFalse(chk.ok)
        self.assertTrue(any("stale" in r for r in chk.reject_reasons))

    def test_suspicious_jump_rejected(self):
        p = synth.geometric(12, 100, 0.002)
        p[7] = p[6] * 1.6     # +60% one-day move -> data/event red flag
        for i in range(8, 12):
            p[i] = p[i - 1] * 1.002
        chk = self._check(self._panel(p))
        self.assertFalse(chk.ok)
        self.assertTrue(any("suspicious_jump" in r for r in chk.reject_reasons))


if __name__ == "__main__":
    unittest.main()
