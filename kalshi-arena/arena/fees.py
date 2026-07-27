"""
Kalshi fee model.
=================

The incumbent bot models the trading fee as a smooth per-contract quantity:

    fee_cents(price) = 7.0 * P * (100 - P) / 10000       # ~1.75c at 50c

That is the right *shape* but it drops the two things that actually decide
whether a small order is profitable:

  1. Kalshi charges the fee **per order, rounded up to the next cent**.
     A 1-contract trade at 50c is charged 2c, not 1.75c — 14% more. At the
     order sizes a small account actually uses (1-5 contracts) the rounding
     is not a rounding error, it *is* the fee difference between +EV and -EV.

  2. Maker and taker are not the same. A resting order that gets filled is
     charged at the maker rate, which on many series is lower than the taker
     rate. A bot that only ever crosses the spread pays the higher rate on
     every single trade and never finds out.

Everything here is expressed in **cents** and integer-exact where Kalshi is
integer-exact, so the breakeven math downstream is not off by a rounding step.

Rates are configuration, not gospel: Kalshi publishes a fee schedule that
varies by series and changes over time. `FeeSchedule` carries the rates so a
caller can pin them to whatever the current published schedule says, and the
default is the widely-documented general formula.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Kalshi's general trading-fee coefficient: fee = rate * C * P * (1-P).
DEFAULT_TAKER_RATE = 0.07
# Maker rate is series-dependent. Default to the same coefficient so the
# model never *flatters* passive execution by assumption -- a bot has to earn
# the maker advantage from the spread it captures, not from a free parameter.
DEFAULT_MAKER_RATE = 0.07


@dataclass(frozen=True)
class FeeSchedule:
    """Fee rates for one series. Rates are the coefficient in rate*C*P*(1-P)."""

    taker_rate: float = DEFAULT_TAKER_RATE
    maker_rate: float = DEFAULT_MAKER_RATE

    def rate_for(self, is_maker: bool) -> float:
        return self.maker_rate if is_maker else self.taker_rate


DEFAULT_SCHEDULE = FeeSchedule()


def trade_fee_cents(
    contracts: int,
    price_cents: float,
    is_maker: bool = False,
    schedule: FeeSchedule = DEFAULT_SCHEDULE,
) -> int:
    """Total fee in whole cents for one order of `contracts` at `price_cents`.

    fee = ceil(rate * C * P * (1 - P))  with P in dollars, result in cents.

    Rounded up to the next whole cent, per order -- which is how Kalshi
    charges it, and why 1-lot trades are more expensive than the smooth
    per-contract formula suggests.
    """
    if contracts <= 0:
        return 0
    p = price_cents / 100.0
    if p <= 0.0 or p >= 1.0:
        # A contract at 0 or 100 has no fee -- there is no uncertainty left
        # to charge for, and the smooth formula agrees (P*(1-P) == 0).
        return 0
    rate = schedule.rate_for(is_maker)
    fee_dollars = rate * contracts * p * (1.0 - p)
    return int(math.ceil(fee_dollars * 100.0 - 1e-9))


def trade_fee_cents_per_contract(
    contracts: int,
    price_cents: float,
    is_maker: bool = False,
    schedule: FeeSchedule = DEFAULT_SCHEDULE,
) -> float:
    """Fee amortised over the order. Use for per-contract EV, not for cash."""
    if contracts <= 0:
        return 0.0
    return trade_fee_cents(contracts, price_cents, is_maker, schedule) / contracts


def smooth_fee_cents_per_contract(
    price_cents: float,
    is_maker: bool = False,
    schedule: FeeSchedule = DEFAULT_SCHEDULE,
) -> float:
    """The incumbent's un-rounded per-contract fee. Kept for comparison."""
    p = price_cents / 100.0
    if p <= 0.0 or p >= 1.0:
        return 0.0
    return schedule.rate_for(is_maker) * p * (1.0 - p) * 100.0


def round_trip_fee_cents(
    contracts: int,
    entry_price_cents: float,
    exit_price_cents: float,
    entry_maker: bool = False,
    exit_maker: bool = False,
    schedule: FeeSchedule = DEFAULT_SCHEDULE,
) -> int:
    """Fee for entering and later closing a position before settlement.

    Holding to settlement costs one fee. Trading out costs two. A bot that
    plans to take profit early must clear *both* or the exit destroys the
    edge it is trying to bank -- which is exactly the trap an entry-only
    fee check walks into.
    """
    return trade_fee_cents(contracts, entry_price_cents, entry_maker, schedule) + trade_fee_cents(
        contracts, exit_price_cents, exit_maker, schedule
    )


def breakeven_prob(
    price_cents: float,
    contracts: int = 1,
    is_maker: bool = False,
    schedule: FeeSchedule = DEFAULT_SCHEDULE,
) -> float:
    """True probability at which buying at `price_cents` is exactly EV-neutral.

    Buy at price c (cents), pay fee f (cents, amortised). Win pays 100.
        EV = p*(100 - c) - (1-p)*c - f = 100p - c - f
    so breakeven is p = (c + f) / 100.

    This is the number the entry gate should compare against, and it is
    strictly worse than the naive c/100 that an unfee'd bot uses.
    """
    f = trade_fee_cents_per_contract(contracts, price_cents, is_maker, schedule)
    return (price_cents + f) / 100.0


def net_edge_cents(
    p_true: float,
    price_cents: float,
    contracts: int = 1,
    is_maker: bool = False,
    schedule: FeeSchedule = DEFAULT_SCHEDULE,
) -> float:
    """Expected profit per contract, in cents, after fees.

    No half-spread term: `price_cents` is the price actually paid, so the
    spread cost is already inside it. The incumbent subtracts the half-spread
    *on top of* using the ask price, double-charging itself for crossing --
    conservative, but it means its stated edge is not the edge it books.
    """
    f = trade_fee_cents_per_contract(contracts, price_cents, is_maker, schedule)
    return p_true * 100.0 - price_cents - f


def max_profitable_price(
    p_true: float,
    contracts: int = 1,
    is_maker: bool = False,
    schedule: FeeSchedule = DEFAULT_SCHEDULE,
) -> float:
    """Highest price still +EV at `p_true`. Useful as a limit-price ceiling."""
    lo, hi = 0.0, 100.0
    for _ in range(40):  # bisection: fee is monotone-ish but has ceil steps
        mid = (lo + hi) / 2.0
        if net_edge_cents(p_true, mid, contracts, is_maker, schedule) > 0:
            lo = mid
        else:
            hi = mid
    return lo
