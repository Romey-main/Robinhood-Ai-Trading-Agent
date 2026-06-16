import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rhbot import ingest
from rhbot.panel import Panel
from rhbot.universe import Membership

ISHARES = """iShares Russell 1000 ETF
Fund Holdings as of,"{date}"
Inception Date,"May 19, 2000"
 ,,,,
Ticker,Name,Sector,Asset Class,Weight (%)
AAPL,Apple Inc,Information Technology,Equity,6.50
MSFT,Microsoft Corp,Information Technology,Equity,5.90
{extra}
-,USD CASH,Cash and/or Derivatives,Cash,0.10
"""


class IngestTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def _write(self, name, text):
        p = os.path.join(self.dir, name)
        with open(p, "w") as fh:
            fh.write(text)
        return p

    def test_membership_from_holdings_dir(self):
        self._write("IWB_2025-01-31.csv",
                    ISHARES.format(date="Jan 31, 2025", extra="NVDA,NVIDIA,IT,Equity,5.0"))
        self._write("IWB_2025-02-28.csv",
                    ISHARES.format(date="Feb 28, 2025", extra="TSLA,Tesla,Cons,Equity,1.5"))
        snaps = ingest.membership_from_holdings_dir(self.dir)
        self.assertEqual(set(snaps), {"2025-01-31", "2025-02-28"})
        # cash line "-" is skipped; real tickers kept
        self.assertEqual(snaps["2025-01-31"], {"AAPL", "MSFT", "NVDA"})
        self.assertIn("TSLA", snaps["2025-02-28"])
        self.assertNotIn("-", snaps["2025-01-31"])

    def test_membership_roundtrips_to_membership_object(self):
        self._write("IWB_2025-01-31.csv",
                    ISHARES.format(date="Jan 31, 2025", extra="NVDA,NVIDIA,IT,Equity,5.0"))
        snaps = ingest.membership_from_holdings_dir(self.dir)
        out = os.path.join(self.dir, "membership.json")
        ingest.write_membership_json(snaps, out)
        m = Membership.from_json(out)
        self.assertTrue(m.has_snapshots)
        # asof after the snapshot resolves to it
        self.assertEqual(m.members_asof("2025-06-01"), {"AAPL", "MSFT", "NVDA"})

    def test_membership_from_long_csv(self):
        p = self._write("m.csv", "date,ticker\n2025-01-31,AAPL\n2025-01-31,MSFT\n")
        snaps = ingest.membership_from_long_csv(p)
        self.assertEqual(snaps["2025-01-31"], {"AAPL", "MSFT"})

    def test_panel_from_long_csv_with_custom_columns(self):
        p = self._write("px.csv",
                        "trade_date,ric,close_adj\n"
                        "2025-01-02,aapl,185.0\n2025-01-03,AAPL,187.0\n"
                        "2025-01-02,MSFT,400.0\n")
        out = os.path.join(self.dir, "panel.csv")
        n = ingest.panel_from_long_csv(p, out, date_col="trade_date",
                                       symbol_col="ric", price_col="close_adj")
        self.assertEqual(n, 3)
        panel = Panel.from_csv(out)
        self.assertEqual(panel.price_asof("AAPL", "2025-01-03"), 187.0)
        self.assertEqual(panel.price_asof("MSFT", "2025-01-02"), 400.0)


if __name__ == "__main__":
    unittest.main()
