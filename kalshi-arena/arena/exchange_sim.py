"""
Paper matching engine.

Every bot gets its own copy of the book. That is a deliberate choice: with a
shared book the winner of the competition would partly be decided by tick
ordering (whoever is polled first eats the only 3 contracts at the touch),
which measures nothing about the strategies. Independent books mean both bots
face identical opportunities and the scoreboard reflects decisions.

The part that has to be right, or the whole arena is a lie, is **passive
execution**. Resting an order to save the spread is free money in a naive
simulator: you always get filled, always at your price, never at a bad time.
In a real book you get filled precisely when someone wants to sell to you,
which is disproportionately when the price is about to go lower. So:

  - A resting buy fills *for certain* when the market trades down through it.
  - A resting buy at the touch fills *probabilistically*, at a rate driven by
    traded volume and the size queued ahead of it.
  - A resting buy behind the touch does not fill at all.

The result is that passive execution earns the spread but pays for it in
adverse selection, which is the actual trade-off a market maker faces. A bot
that quotes passively has to be right about *direction* to profit from it,
not merely patient.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from .fees import DEFAULT_SCHEDULE, FeeSchedule, trade_fee_cents
from .portfolio import Portfolio
from .types import Fill, MarketSnapshot, Order, OrderStyle, Resolution, Side


@dataclass
class RestingOrder:
    order: Order
    placed_tick: int
    remaining: int

    @property
    def expires_tick(self) -> int:
        return self.placed_tick + self.order.ttl_ticks


@dataclass
class ExchangeConfig:
    schedule: FeeSchedule = DEFAULT_SCHEDULE
    # Volume that must trade at the touch for a queued order to be ~63% likely
    # to fill in one tick, scaled by the size ahead of it.
    fill_volume_scale: float = 250.0
    # Taker orders can only consume the size displayed at the touch. Set
    # False to allow unlimited size (useful for isolating strategy effects).
    respect_displayed_size: bool = True
    # Extra cents of slippage when a taker order is larger than displayed size
    # and has to walk the book.
    walk_book_slippage_cents: int = 1
    allow_short: bool = False


class PaperExchange:
    """Simulated exchange for one bot. Deterministic given a seed."""

    def __init__(self, portfolio: Portfolio, config: ExchangeConfig | None = None, seed: int = 0):
        self.pf = portfolio
        self.cfg = config or ExchangeConfig()
        self.rng = random.Random(seed)
        self.resting: dict = {}  # ticker -> list[RestingOrder]
        self.rejected: list = []
        self.settled: set = set()

    # -- order entry ------------------------------------------------------

    def submit(self, orders: list, books: dict, t: int) -> list:
        """Process this tick's orders. Returns fills that happened immediately."""
        fills = []
        for order in orders:
            snap = books.get(order.ticker)
            if snap is None:
                self.rejected.append((order, "no book"))
                continue
            if order.contracts <= 0:
                continue
            if order.style is OrderStyle.TAKE:
                f = self._take(order, snap, t)
                if f:
                    fills.append(f)
            else:
                self._rest(order, snap, t)
        return fills

    def _take(self, order: Order, snap: MarketSnapshot, t: int):
        """Cross the spread. Fills now, at the offer, paying the taker fee."""
        if order.is_exit:
            return self._take_exit(order, snap, t)

        ask = snap.ask_for(order.side)
        if ask <= 0 or ask >= 100:
            self.rejected.append((order, "unquoted"))
            return None
        if ask > order.limit_cents:
            self.rejected.append((order, f"ask {ask} > limit {order.limit_cents}"))
            return None

        n = order.contracts
        price = ask
        if self.cfg.respect_displayed_size:
            avail = snap.size_at_ask(order.side)
            if avail <= 0:
                self.rejected.append((order, "no size at ask"))
                return None
            if n > avail:
                # Walk the book: the excess pays up a tick. Bots that size
                # past displayed liquidity should feel it.
                price = min(99, ask + self.cfg.walk_book_slippage_cents)
                if price > order.limit_cents:
                    n = avail
                    price = ask

        cost = n * price
        fee = trade_fee_cents(n, price, is_maker=False, schedule=self.cfg.schedule)
        if cost + fee > self.pf.cash_cents:
            n = max(0, (self.pf.cash_cents - fee) // price)
            if n <= 0:
                self.rejected.append((order, "insufficient cash"))
                return None
            fee = trade_fee_cents(n, price, is_maker=False, schedule=self.cfg.schedule)

        fill = Fill(
            ticker=order.ticker,
            side=order.side,
            contracts=n,
            price_cents=price,
            fee_cents=fee,
            is_maker=False,
            t=t,
            is_exit=False,
            p_est=order.p_est,
            reason=order.reason,
        )
        self.pf.apply_open(fill, cluster=snap.cluster)
        return fill

    def _take_exit(self, order: Order, snap: MarketSnapshot, t: int):
        """Sell an existing position into the bid."""
        held = self.pf.contracts_in(order.ticker, order.side)
        n = min(order.contracts, held)
        if n <= 0:
            return None
        bid = snap.bid_for(order.side)
        if bid <= 0:
            self.rejected.append((order, "no bid to sell into"))
            return None
        if bid < order.limit_cents:
            self.rejected.append((order, f"bid {bid} < limit {order.limit_cents}"))
            return None
        fee = trade_fee_cents(n, bid, is_maker=False, schedule=self.cfg.schedule)
        fill = Fill(
            ticker=order.ticker,
            side=order.side,
            contracts=n,
            price_cents=bid,
            fee_cents=fee,
            is_maker=False,
            t=t,
            is_exit=True,
            p_est=order.p_est,
            reason=order.reason,
        )
        self.pf.apply_close(fill)
        return fill

    def _rest(self, order: Order, snap: MarketSnapshot, t: int) -> None:
        """Queue a passive order. It may never fill -- that is the point."""
        if order.is_exit:
            held = self.pf.contracts_in(order.ticker, order.side)
            if held <= 0:
                return
            order.contracts = min(order.contracts, held)
        else:
            # Reserve nothing: cash is checked at fill time, matching how a
            # real exchange would reject on insufficient funds rather than
            # at submission in this simplified model.
            pass
        self.resting.setdefault(order.ticker, []).append(
            RestingOrder(order=order, placed_tick=t, remaining=order.contracts)
        )

    # -- passive fills ----------------------------------------------------

    def match_resting(self, books: dict, t: int) -> list:
        """Attempt to fill queued orders against the new book state."""
        fills = []
        for ticker, queue in list(self.resting.items()):
            snap = books.get(ticker)
            if snap is None:
                continue
            keep = []
            for ro in queue:
                if t >= ro.expires_tick:
                    continue  # cancelled, unfilled
                f = self._try_fill_resting(ro, snap, t)
                if f:
                    fills.append(f)
                if ro.remaining > 0:
                    keep.append(ro)
            if keep:
                self.resting[ticker] = keep
            else:
                self.resting.pop(ticker, None)
        return fills

    def _try_fill_resting(self, ro: RestingOrder, snap: MarketSnapshot, t: int):
        o = ro.order
        limit = o.limit_cents

        if o.is_exit:
            # Resting *sell* of a held position at `limit`.
            best_bid = snap.bid_for(o.side)
            best_ask = snap.ask_for(o.side)
            if best_bid >= limit:
                p_fill = 1.0  # buyers have come up to our offer
            elif best_ask <= limit:
                p_fill = self._touch_fill_prob(snap, o.side, resting_buy=False)
            else:
                return None
        else:
            # Resting *buy* at `limit`.
            best_ask = snap.ask_for(o.side)
            best_bid = snap.bid_for(o.side)
            if best_ask <= limit:
                # The market traded down through our bid: we are certain to
                # be filled, and certain to be filled *badly*. This is the
                # adverse selection that makes passive execution honest.
                p_fill = 1.0
            elif best_bid <= limit:
                p_fill = self._touch_fill_prob(snap, o.side, resting_buy=True)
            else:
                return None

        if self.rng.random() > p_fill:
            return None

        n = ro.remaining
        if o.is_exit:
            held = self.pf.contracts_in(o.ticker, o.side)
            n = min(n, held)
            if n <= 0:
                ro.remaining = 0
                return None
            fee = trade_fee_cents(n, limit, is_maker=True, schedule=self.cfg.schedule)
            fill = Fill(
                ticker=o.ticker,
                side=o.side,
                contracts=n,
                price_cents=limit,
                fee_cents=fee,
                is_maker=True,
                t=t,
                is_exit=True,
                p_est=o.p_est,
                reason=o.reason,
            )
            self.pf.apply_close(fill)
        else:
            fee = trade_fee_cents(n, limit, is_maker=True, schedule=self.cfg.schedule)
            if n * limit + fee > self.pf.cash_cents:
                n = max(0, (self.pf.cash_cents - fee) // max(1, limit))
                if n <= 0:
                    ro.remaining = 0
                    return None
                fee = trade_fee_cents(n, limit, is_maker=True, schedule=self.cfg.schedule)
            fill = Fill(
                ticker=o.ticker,
                side=o.side,
                contracts=n,
                price_cents=limit,
                fee_cents=fee,
                is_maker=True,
                t=t,
                is_exit=False,
                p_est=o.p_est,
                reason=o.reason,
            )
            self.pf.apply_open(fill, cluster=snap.cluster)

        ro.remaining -= n
        return fill

    def _touch_fill_prob(self, snap: MarketSnapshot, side: Side, resting_buy: bool) -> float:
        """Queue-position model for an order sitting at the best price.

        Fill intensity rises with traded volume and falls with the size
        already queued ahead. Expressed as 1 - exp(-lambda) so it saturates
        rather than exceeding 1 in busy markets.
        """
        queued = snap.size_at_ask(side.other) if resting_buy else snap.size_at_ask(side)
        queued = max(1, queued)
        vol = max(0, snap.volume)
        lam = (vol / self.cfg.fill_volume_scale) / queued
        return 1.0 - math.exp(-lam)

    def cancel_all(self, ticker: str | None = None) -> None:
        if ticker is None:
            self.resting.clear()
        else:
            self.resting.pop(ticker, None)

    # -- settlement -------------------------------------------------------

    def settle(self, resolutions: list) -> int:
        pnl = 0
        for res in resolutions:
            if res.ticker in self.settled:
                continue
            self.settled.add(res.ticker)
            self.cancel_all(res.ticker)
            pnl += self.pf.settle(res)
        return pnl
