"""
The shared knowledge base.

This is the thing that makes the arena more than two backtests side by side.
Both bots write everything they see into one SQLite file, and both are
allowed to read all of it. Three kinds of knowledge accumulate:

  **Observations.** Every market that resolved, with the price, each bot's
  forecast, and the outcome. Pooled, this is a calibration dataset that
  neither bot could have built alone -- and crucially it includes markets a
  bot never traded and never would have traded, which is exactly the region
  where its forecasts are least tested.

  **Trades.** Every fill with its segment tags and its realised outcome.
  This is what the significance tests in `transfer.py` consume: not "who won
  the round" but "who traded 65-85c markets better, over how many contracts,
  and is the difference bigger than the noise".

  **Lessons.** Human-readable, evidence-tagged findings mined from the above,
  written to `LESSONS.md`. These exist because a parameter delta is not
  understanding: "kelly_fraction 0.18 -> 0.21" tells you nothing, while
  "crossing the spread on 85c+ markets lost 2.3c/contract over 412 contracts,
  p=0.004" tells you what to change and why.

Everything is append-only and keyed by round, so the whole history of how the
bots got where they are stays inspectable.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS rounds (
    round INTEGER, bot TEXT, ts TEXT,
    params_json TEXT, score_json TEXT,
    PRIMARY KEY (round, bot)
);
CREATE TABLE IF NOT EXISTS observations (
    round INTEGER, bot TEXT, ticker TEXT, series TEXT,
    p_market REAL, p_signal REAL, p_final REAL, outcome INTEGER,
    regime TEXT
);
CREATE INDEX IF NOT EXISTS idx_obs_bot ON observations(bot);
CREATE INDEX IF NOT EXISTS idx_obs_round ON observations(round);

CREATE TABLE IF NOT EXISTS trades (
    round INTEGER, bot TEXT, ticker TEXT, side TEXT,
    price INTEGER, contracts INTEGER, fee INTEGER,
    is_maker INTEGER, is_exit INTEGER,
    price_bucket TEXT, spread_bucket TEXT, regime TEXT,
    outcome INTEGER, pnl_cents REAL, pnl_per_contract REAL
);
CREATE INDEX IF NOT EXISTS idx_tr_bot ON trades(bot);
CREATE INDEX IF NOT EXISTS idx_tr_seg ON trades(price_bucket, is_maker);

CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    round INTEGER, source_bot TEXT, scope TEXT, kind TEXT,
    statement TEXT, support_n INTEGER,
    effect REAL, p_value REAL, confidence REAL
);
CREATE TABLE IF NOT EXISTS transfers (
    round INTEGER, from_bot TEXT, to_bot TEXT, param TEXT,
    old_value TEXT, new_value TEXT, weight REAL, evidence TEXT
);
CREATE TABLE IF NOT EXISTS knowledge_blobs (
    round INTEGER, bot TEXT, blob_json TEXT,
    PRIMARY KEY (round, bot)
);
"""


@dataclass
class Lesson:
    round: int
    source_bot: str
    scope: str
    kind: str
    statement: str
    support_n: int
    effect: float
    p_value: float
    confidence: float


class KnowledgeBase:
    def __init__(self, path: str | Path = "knowledge/kb.sqlite"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # -- writes -----------------------------------------------------------

    def record_round(self, round_index: int, bot: str, params: dict, score: dict, ts: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO rounds (round,bot,ts,params_json,score_json) VALUES (?,?,?,?,?)",
            (round_index, bot, ts, json.dumps(params, default=str), json.dumps(score, default=str)),
        )
        self.conn.commit()

    def record_observations(self, rows: list) -> None:
        self.conn.executemany(
            "INSERT INTO observations (round,bot,ticker,series,p_market,p_signal,p_final,outcome,regime)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            rows,
        )
        self.conn.commit()

    def record_trades(self, rows: list) -> None:
        self.conn.executemany(
            "INSERT INTO trades (round,bot,ticker,side,price,contracts,fee,is_maker,is_exit,"
            "price_bucket,spread_bucket,regime,outcome,pnl_cents,pnl_per_contract)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        self.conn.commit()

    def record_lesson(self, lesson: Lesson) -> None:
        self.conn.execute(
            "INSERT INTO lessons (round,source_bot,scope,kind,statement,support_n,effect,p_value,confidence)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (
                lesson.round,
                lesson.source_bot,
                lesson.scope,
                lesson.kind,
                lesson.statement,
                lesson.support_n,
                lesson.effect,
                lesson.p_value,
                lesson.confidence,
            ),
        )
        self.conn.commit()

    def record_transfer(
        self, round_index: int, frm: str, to: str, param: str, old, new, weight: float, evidence: dict
    ) -> None:
        self.conn.execute(
            "INSERT INTO transfers (round,from_bot,to_bot,param,old_value,new_value,weight,evidence)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (round_index, frm, to, param, str(old), str(new), weight, json.dumps(evidence, default=str)),
        )
        self.conn.commit()

    def record_blob(self, round_index: int, bot: str, blob: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO knowledge_blobs (round,bot,blob_json) VALUES (?,?,?)",
            (round_index, bot, json.dumps(blob)),
        )
        self.conn.commit()

    # -- reads ------------------------------------------------------------

    def pooled_observations(self, exclude_bot: str | None = None, limit: int | None = None) -> list:
        """Resolutions seen by *any* bot, deduplicated by (round, ticker).

        This is the pooled calibration set. Deduplication matters: two bots
        watching the same market is one observation of the world, not two,
        and counting it twice would make every posterior twice as confident
        as the evidence supports.
        """
        q = (
            "SELECT round, ticker, MIN(p_market) AS p_market, AVG(p_signal) AS p_signal,"
            " MAX(outcome) AS outcome FROM observations"
        )
        params: list = []
        if exclude_bot:
            q += " WHERE bot != ?"
            params.append(exclude_bot)
        q += " GROUP BY round, ticker ORDER BY round DESC"
        if limit:
            q += f" LIMIT {int(limit)}"
        return [dict(r) for r in self.conn.execute(q, params).fetchall()]

    def segment_pnl(self, bot: str, rounds: list | None = None, **filters) -> list:
        """Per-contract P&L samples for a bot in a segment, as a flat list.

        Returns one value per contract so significance tests see the real
        sample size. A 40-contract fill is 40 observations of the same bet,
        which is not 40 independent samples -- but it *is* 40 units of money
        at risk, and weighting by money is the right call when the question
        is "which policy makes more".
        """
        q = "SELECT pnl_per_contract, contracts FROM trades WHERE bot = ? AND is_exit = 0"
        params: list = [bot]
        for k, v in filters.items():
            if v is None:
                continue
            q += f" AND {k} = ?"
            params.append(v)
        if rounds:
            q += " AND round IN (" + ",".join("?" * len(rounds)) + ")"
            params.extend(rounds)
        out = []
        for row in self.conn.execute(q, params).fetchall():
            out.extend([row["pnl_per_contract"]] * max(1, int(row["contracts"])))
        return out

    def segments_seen(self) -> list:
        rows = self.conn.execute(
            "SELECT DISTINCT price_bucket, is_maker, regime FROM trades WHERE is_exit = 0"
        ).fetchall()
        return [(r["price_bucket"], r["is_maker"], r["regime"]) for r in rows]

    def latest_blob(self, bot: str) -> dict:
        row = self.conn.execute(
            "SELECT blob_json FROM knowledge_blobs WHERE bot = ? ORDER BY round DESC LIMIT 1", (bot,)
        ).fetchone()
        return json.loads(row["blob_json"]) if row else {}

    def lessons(self, limit: int = 50) -> list:
        rows = self.conn.execute(
            "SELECT * FROM lessons ORDER BY round DESC, confidence DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def transfers(self, limit: int = 200) -> list:
        rows = self.conn.execute(
            "SELECT * FROM transfers ORDER BY round DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def round_scores(self) -> list:
        rows = self.conn.execute("SELECT * FROM rounds ORDER BY round, bot").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["score"] = json.loads(d.pop("score_json"))
            d["params"] = json.loads(d.pop("params_json"))
            out.append(d)
        return out
