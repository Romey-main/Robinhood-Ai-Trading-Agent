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

    def test_tickers_from_holdings_file(self):
        p = self._write("IWB_2025-03-31.csv",
                        ISHARES.format(date="Mar 31, 2025", extra="GE,GE Aerospace,Ind,Equity,0.4"))
        t = ingest.tickers_from_holdings_file(p)
        self.assertEqual(t, {"AAPL", "MSFT", "GE"})

    def test_append_membership_snapshot_accumulates(self):
        out = os.path.join(self.dir, "membership.json")
        self.assertEqual(ingest.append_membership_snapshot(out, "2025-01-31", {"AAPL", "MSFT"}), 1)
        self.assertEqual(ingest.append_membership_snapshot(out, "2025-02-28", {"AAPL", "NVDA"}), 2)
        m = Membership.from_json(out)
        self.assertEqual(m.members_asof("2025-03-01"), {"AAPL", "NVDA"})
        self.assertEqual(m.members_asof("2025-02-01"), {"AAPL", "MSFT"})

    def test_build_panel_csv_with_injected_fetch(self):
        series = {
            "AAA": [("2025-01-02", 10.0), ("2025-01-03", 11.0)],
            "BBB": [("2025-01-02", 20.0), ("2025-01-03", 19.0)],
        }
        out = os.path.join(self.dir, "panel.csv")
        n = ingest.build_panel_csv(["AAA", "BBB"], "2025-01-01", out,
                                   fetch=lambda s, start: series[s])
        self.assertEqual(n, 4)
        panel = Panel.from_csv(out)
        self.assertEqual(panel.price_asof("AAA", "2025-01-03"), 11.0)
        self.assertEqual(panel.price_asof("BBB", "2025-01-02"), 20.0)

    def test_tiingo_fetch_parses_adjclose(self):
        payload = ('[{"date":"2025-01-02T00:00:00.000Z","adjClose":10.5},'
                   '{"date":"2025-01-03T00:00:00.000Z","adjClose":11.25}]')
        rows = ingest.tiingo_fetch("AAPL", "2025-01-01", token="x",
                                   http_get=lambda url, headers=None: payload)
        self.assertEqual(rows, [("2025-01-02", 10.5), ("2025-01-03", 11.25)])

    def test_tiingo_requires_token(self):
        # no token arg and no env var -> clear error, not a silent bad fetch
        os.environ.pop("TIINGO_API_KEY", None)
        with self.assertRaises(RuntimeError):
            ingest.tiingo_fetch("AAPL", "2025-01-01")

    def test_stooq_fetch_respects_start(self):
        csv_text = ("Date,Open,High,Low,Close,Volume\n"
                    "2024-12-31,1,1,1,9.0,100\n"
                    "2025-01-02,1,1,1,20.0,100\n2025-01-03,1,1,1,21.0,100\n")
        rows = ingest.stooq_fetch("MSFT", "2025-01-01",
                                  http_get=lambda url, headers=None: csv_text)
        self.assertEqual(rows, [("2025-01-02", 20.0), ("2025-01-03", 21.0)])

    def test_diff_snapshots(self):
        snaps = {"2025-01-31": ["AAPL", "MSFT"], "2025-02-28": ["AAPL", "NVDA"]}
        self.assertEqual(ingest.two_latest_dates(snaps), ("2025-01-31", "2025-02-28"))
        d = ingest.diff_snapshots(snaps, "2025-01-31", "2025-02-28")
        self.assertEqual(d["added"], ["NVDA"])
        self.assertEqual(d["removed"], ["MSFT"])
        self.assertEqual(d["unchanged"], 1)

    def _write_xlsx(self, name, rows):
        import zipfile
        strings, idx = [], {}

        def sid(s):
            if s not in idx:
                idx[s] = len(strings)
                strings.append(s)
            return idx[s]

        sheet_rows = []
        for ri, row in enumerate(rows, start=1):
            cells = "".join(
                f'<c r="{chr(ord("A") + ci)}{ri}" t="s"><v>{sid(v)}</v></c>'
                for ci, v in enumerate(row))
            sheet_rows.append(f'<row r="{ri}">{cells}</row>')
        ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        sst = f"<sst {ns}>" + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>"
        sheet = f"<worksheet {ns}><sheetData>{''.join(sheet_rows)}</sheetData></worksheet>"
        p = os.path.join(self.dir, name)
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("xl/sharedStrings.xml", sst)
            z.writestr("xl/worksheets/sheet1.xml", sheet)
        return p

    def test_tickers_from_xlsx_holdings(self):
        # iShares now serves .xlsx; the same parser must handle it (preamble +
        # header + cash row), columns aligned by cell reference.
        p = self._write_xlsx("IWB_2026-06-16.xlsx", [
            ["iShares Russell 1000 ETF"],
            ["Ticker", "Name", "Asset Class"],
            ["AAPL", "Apple Inc", "Equity"],
            ["MSFT", "Microsoft Corp", "Equity"],
            ["-", "USD CASH", "Cash"],
        ])
        self.assertEqual(ingest.tickers_from_holdings_file(p), {"AAPL", "MSFT"})
        # and through the dir-based builder (xlsx glob + date-in-filename)
        snaps = ingest.membership_from_holdings_dir(self.dir, glob_pat="*.xlsx")
        self.assertEqual(snaps["2026-06-16"], {"AAPL", "MSFT"})


if __name__ == "__main__":
    unittest.main()
