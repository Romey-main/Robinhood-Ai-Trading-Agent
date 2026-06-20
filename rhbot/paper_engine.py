"""Paper-trading ledger — the honesty layer.

This is where the strategy has to *earn its keep* before any real money is
risked. It records simulated trades, marks them against later prices (closing
on stop/target), and reports the statistics that actually matter: win rate,
average win vs average loss, expectancy, profit factor, and max drawdown.

A high win rate alone is meaningless — you can win 90% of the time and still
go broke if the 10% of losers are large. Expectancy and profit factor are the
numbers that tell you whether the edge is real.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

from .models import PaperTrade


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PaperLedger:
    def __init__(self, path: str):
        self.path = path
        self.trades: list[PaperTrade] = []
        self.load()

    # --- persistence ----------------------------------------------------
    def load(self) -> None:
        self.trades = []
        if not os.path.exists(self.path):
            return
        with open(self.path) as fh:
            text = fh.read().strip()
        if not text:            # empty/freshly-created file == no trades
            return
        self.trades = [PaperTrade.from_dict(d) for d in json.loads(text)]

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w") as fh:
            json.dump([t.to_dict() for t in self.trades], fh, indent=2)

    # --- mutations ------------------------------------------------------
    @property
    def open_trades(self) -> list[PaperTrade]:
        return [t for t in self.trades if t.is_open]

    @property
    def closed_trades(self) -> list[PaperTrade]:
        return [t for t in self.trades if not t.is_open]

    def open_trade(
        self,
        symbol: str,
        qty: float,
        entry_price: float,
        stop_price: float,
        target_price: float,
        rationale: str = "",
        sentiment: float = 0.0,
        score: float = 0.0,
        entry_time: Optional[str] = None,
    ) -> PaperTrade:
        t = PaperTrade(
            symbol=symbol,
            qty=qty,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            entry_time=entry_time or _now(),
            rationale=rationale,
            sentiment=sentiment,
            score=score,
        )
        self.trades.append(t)
        return t

    def close_trade(
        self, trade_id: str, exit_price: float, reason: str = "manual",
        exit_time: Optional[str] = None,
    ) -> Optional[PaperTrade]:
        for t in self.trades:
            if t.id == trade_id and t.is_open:
                t.exit_price = round(float(exit_price), 4)
                t.exit_reason = reason
                t.exit_time = exit_time or _now()
                t.status = "closed"
                return t
        return None

    def mark(
        self, symbol: str, last: float, high: Optional[float] = None,
        low: Optional[float] = None, as_of: Optional[str] = None,
    ) -> list[PaperTrade]:
        """Mark open trades on ``symbol`` against a new bar.

        If high/low are provided we check whether the bar's range crossed the
        stop or target (the realistic way to fill). When both are touched in
        the same bar we assume the *stop* filled first — the conservative,
        don't-fool-yourself choice. Without high/low we fall back to ``last``.
        """
        touched: list[PaperTrade] = []
        for t in self.open_trades:
            if t.symbol != symbol:
                continue
            hi = high if high is not None else last
            lo = low if low is not None else last
            if lo <= t.stop_price:
                self.close_trade(t.id, t.stop_price, "stop", as_of)
                touched.append(t)
            elif hi >= t.target_price:
                self.close_trade(t.id, t.target_price, "target", as_of)
                touched.append(t)
        return touched

    # --- analytics ------------------------------------------------------
    def stats(self) -> dict:
        closed = self.closed_trades
        n = len(closed)
        out = {
            "n_total": len(self.trades),
            "n_open": len(self.open_trades),
            "n_closed": n,
            "win_rate": None,
            "n_wins": 0,
            "n_losses": 0,
            "avg_win_pct": None,
            "avg_loss_pct": None,
            "expectancy_pct": None,
            "profit_factor": None,
            "total_pnl": 0.0,
            "max_drawdown": 0.0,
            "note": "Need a meaningful sample (aim for >=20-30 closed trades) "
                    "before trusting these numbers.",
        }
        if n == 0:
            return out

        wins = [t for t in closed if (t.pnl() or 0) > 0]
        losses = [t for t in closed if (t.pnl() or 0) <= 0]
        out["n_wins"] = len(wins)
        out["n_losses"] = len(losses)
        out["win_rate"] = round(len(wins) / n, 4)
        out["total_pnl"] = round(sum(t.pnl() or 0 for t in closed), 2)

        if wins:
            out["avg_win_pct"] = round(
                sum(t.pnl_pct() or 0 for t in wins) / len(wins), 4)
        if losses:
            out["avg_loss_pct"] = round(
                sum(t.pnl_pct() or 0 for t in losses) / len(losses), 4)

        wr = out["win_rate"]
        aw = out["avg_win_pct"] or 0.0
        al = out["avg_loss_pct"] or 0.0
        out["expectancy_pct"] = round(wr * aw + (1 - wr) * al, 4)

        gross_win = sum(t.pnl() or 0 for t in wins)
        gross_loss = abs(sum(t.pnl() or 0 for t in losses))
        out["profit_factor"] = (
            round(gross_win / gross_loss, 2) if gross_loss > 0 else None)

        # max drawdown on the realized-PnL equity curve, ordered by exit
        seq = sorted(closed, key=lambda t: t.exit_time or "")
        equity, peak, mdd = 0.0, 0.0, 0.0
        for t in seq:
            equity += t.pnl() or 0
            peak = max(peak, equity)
            mdd = min(mdd, equity - peak)
        out["max_drawdown"] = round(mdd, 2)
        return out
