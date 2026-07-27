"""Cash, positions and exposure accounting for one bot."""

from __future__ import annotations

from dataclasses import dataclass, field

from .types import Fill, MarketSnapshot, Position, Resolution, Side


@dataclass
class Portfolio:
    """Single-bot book.

    YES and NO are held as separate positions rather than netted into a
    signed YES exposure. Netting is tempting and wrong for Kalshi: holding
    one YES and one NO is not a flat book, it is a locked $1.00 payout that
    cost you two premiums plus two fees. Keeping them separate makes that
    cost visible instead of hiding it in a zero.
    """

    cash_cents: int
    start_cents: int
    positions: dict = field(default_factory=dict)  # (ticker, Side) -> Position
    fills: list = field(default_factory=list)
    realized_pnl_cents: int = 0
    fees_paid_cents: int = 0
    high_water_cents: int = 0

    def __post_init__(self):
        if not self.high_water_cents:
            self.high_water_cents = self.cash_cents

    # -- exposure ---------------------------------------------------------

    def position(self, ticker: str, side: Side):
        return self.positions.get((ticker, side))

    def contracts_in(self, ticker: str, side: Side) -> int:
        p = self.positions.get((ticker, side))
        return p.contracts if p else 0

    def market_exposure_cents(self, ticker: str) -> int:
        return sum(
            p.cost_basis_cents for (tk, _), p in self.positions.items() if tk == ticker
        )

    def cluster_exposure_cents(self, cluster: str) -> int:
        if not cluster:
            return 0
        return sum(p.cost_basis_cents for p in self.positions.values() if p.cluster == cluster)

    @property
    def open_exposure_cents(self) -> int:
        return sum(p.cost_basis_cents for p in self.positions.values())

    @property
    def n_positions(self) -> int:
        return sum(1 for p in self.positions.values() if p.contracts > 0)

    # -- marking ----------------------------------------------------------

    def mark_to_market_cents(self, books: dict) -> int:
        """Equity = cash + mark of open positions at the mid.

        Marking at the mid rather than at the bid is the standard choice and
        the honest one here: marking at the bid would make every freshly
        opened position show an instant loss equal to the spread, and a
        drawdown ladder reading that mark would deleverage the bot for the
        crime of having traded at all.
        """
        total = self.cash_cents
        for (ticker, side), pos in self.positions.items():
            if pos.contracts <= 0:
                continue
            snap = books.get(ticker)
            if snap is None:
                total += pos.cost_basis_cents  # stale: hold at cost
                continue
            mid_yes = snap.mid
            mark = mid_yes if side is Side.YES else (100.0 - mid_yes)
            total += int(round(mark * pos.contracts))
        return total

    def liquidation_value_cents(self, books: dict) -> int:
        """What the book is worth if unwound right now, at the bid, after fees."""
        from .fees import trade_fee_cents

        total = self.cash_cents
        for (ticker, side), pos in self.positions.items():
            if pos.contracts <= 0:
                continue
            snap = books.get(ticker)
            if snap is None:
                total += pos.cost_basis_cents
                continue
            bid = snap.bid_for(side)
            total += bid * pos.contracts - trade_fee_cents(pos.contracts, bid, is_maker=False)
        return total

    # -- mutation ---------------------------------------------------------

    def apply_open(self, fill: Fill, cluster: str) -> None:
        key = (fill.ticker, fill.side)
        pos = self.positions.get(key)
        if pos is None:
            pos = Position(
                ticker=fill.ticker,
                side=fill.side,
                cluster=cluster,
                p_est_at_entry=fill.p_est,
                entry_tick=fill.t,
            )
            self.positions[key] = pos
        pos.contracts += fill.contracts
        pos.cost_basis_cents += fill.contracts * fill.price_cents
        pos.fees_paid_cents += fill.fee_cents

        self.cash_cents -= fill.cost_cents
        self.fees_paid_cents += fill.fee_cents
        self.fills.append(fill)

    def apply_close(self, fill: Fill) -> int:
        """Sell contracts back. Returns realised P&L in cents (after fees)."""
        key = (fill.ticker, fill.side)
        pos = self.positions.get(key)
        if pos is None or pos.contracts <= 0:
            return 0
        n = min(fill.contracts, pos.contracts)
        basis = int(round(pos.avg_price * n))
        proceeds = n * fill.price_cents - fill.fee_cents
        pnl = proceeds - basis

        pos.contracts -= n
        pos.cost_basis_cents -= basis
        if pos.contracts <= 0:
            self.positions.pop(key, None)

        self.cash_cents += proceeds
        self.fees_paid_cents += fill.fee_cents
        self.realized_pnl_cents += pnl
        self.fills.append(fill)
        return pnl

    def settle(self, res: Resolution) -> int:
        """Resolve every position on a market. Returns realised P&L in cents."""
        pnl = 0
        for side in (Side.YES, Side.NO):
            pos = self.positions.pop((res.ticker, side), None)
            if pos is None or pos.contracts <= 0:
                continue
            won = (res.outcome == 1) if side is Side.YES else (res.outcome == 0)
            payout = 100 * pos.contracts if won else 0
            self.cash_cents += payout
            pnl += payout - pos.cost_basis_cents
            self.realized_pnl_cents += payout - pos.cost_basis_cents
        return pnl

    def touch_high_water(self, equity_cents: int) -> None:
        if equity_cents > self.high_water_cents:
            self.high_water_cents = equity_cents
