"""
Position sizing.

The incumbent sizes with textbook fractional Kelly:

    f* = p - (1 - p) * c / (1 - c)
    stake = kelly_fraction * f* * bankroll, capped at 2-7% per market

Two things are missing, and both of them cost real money.

**Fees are not in the Kelly payoff.** The incumbent checks fees at the
entry gate and then sizes as though the trade pays a clean 100c. Kelly is a
statement about the actual payoff distribution; feeding it a payoff you will
not receive makes it oversize every position by roughly the fee fraction of
the edge -- which, on a 3c edge with a 1.75c fee, is not a rounding error.

**Estimation error is not in the estimate.** Kelly assumes p is *known*. It
is not: it is the output of a model with a posterior. The classic result is
that using a point estimate with variance sigma^2 overbets, and the
correction is to size on a lower confidence bound rather than the mean. This
is why real desks run quarter-Kelly and still feel overlevered -- fractional
Kelly is a crude proxy for the uncertainty adjustment that belongs inside.

Both corrections shrink positions. Neither is optional if the goal is to
still have a bankroll in a month.
"""

from __future__ import annotations

from dataclasses import dataclass

from .fees import DEFAULT_SCHEDULE, FeeSchedule, trade_fee_cents_per_contract


def kelly_fraction_binary(p: float, price_cents: float, fee_cents_per_contract: float = 0.0) -> float:
    """Optimal bankroll fraction for a binary contract, fees included.

    Buy at c cents, pay fee f cents. Per contract:
        win  (prob p):   +(100 - c - f) cents
        lose (prob 1-p): -(c + f) cents

    Writing the stake as the full downside (c + f, the cash actually at
    risk), the odds received are b = (100 - c - f) / (c + f) and Kelly is
    the standard f* = (p*b - (1-p)) / b.
    """
    c = float(price_cents)
    f = float(fee_cents_per_contract)
    risk = c + f
    win = 100.0 - c - f
    if risk <= 0 or win <= 0:
        return 0.0
    b = win / risk
    f_star = (p * b - (1.0 - p)) / b
    return max(0.0, f_star)


def uncertainty_adjusted_p(p: float, sigma: float, z: float = 1.0) -> float:
    """Lower confidence bound on p, floored at 0.

    `z` controls how paranoid the sizing is about its own estimate. z=0
    reproduces naive Kelly on the point estimate; z=1 sizes on roughly a
    one-sigma-pessimistic view, which is the behaviour a bot with a
    self-measured calibration error should have.
    """
    return max(0.0, min(1.0, p - z * max(0.0, sigma)))


@dataclass
class SizingLimits:
    """Hard caps applied after Kelly. Kelly says how much you *want*; these
    say how much the account can survive being wrong about."""

    kelly_fraction: float = 0.25
    max_pct_per_market: float = 0.05
    max_pct_per_cluster: float = 0.12
    # Total premium at risk across the whole book. Per-market and per-cluster
    # caps do not add up to a portfolio limit: with twenty clusters, a 12%
    # cluster cap permits 240% gross exposure. Without this line the bot is
    # only bounded by its cash, which is not a risk policy -- it is the
    # absence of one.
    max_gross_exposure_pct: float = 0.35
    max_contracts_per_order: int = 25
    min_contracts: int = 1
    # Fraction of bankroll that must stay in cash regardless of opportunity.
    cash_reserve_pct: float = 0.10


def size_position(
    p: float,
    price_cents: float,
    bankroll_cents: int,
    limits: SizingLimits,
    sigma: float = 0.0,
    z: float = 1.0,
    size_multiplier: float = 1.0,
    cluster_exposure_cents: int = 0,
    market_exposure_cents: int = 0,
    gross_exposure_cents: int = 0,
    available_cash_cents: int | None = None,
    is_maker: bool = False,
    schedule: FeeSchedule = DEFAULT_SCHEDULE,
) -> int:
    """Contracts to buy. Returns 0 when the trade is not worth doing.

    Fee-awareness is circular -- the per-contract fee depends on order size,
    which depends on the fee -- so this solves it by sizing once on a
    1-contract fee estimate (the most pessimistic, because of the per-order
    ceiling) and then re-checking that the chosen size is still +EV. That
    ordering is deliberate: it can only ever be too conservative, never too
    aggressive.
    """
    if bankroll_cents <= 0 or not (0 < price_cents < 100):
        return 0

    f1 = trade_fee_cents_per_contract(1, price_cents, is_maker, schedule)
    p_eff = uncertainty_adjusted_p(p, sigma, z)
    f_star = kelly_fraction_binary(p_eff, price_cents, f1)
    if f_star <= 0:
        return 0

    stake = limits.kelly_fraction * f_star * bankroll_cents * max(0.0, size_multiplier)

    # Per-market cap counts what is *already on* in this market, not just the
    # order being sized. Applying the cap to each order in isolation lets a
    # bot rebuild a full-size position every tick and end up many times over
    # its own limit -- the cap silently becomes a per-order cap instead.
    market_room = limits.max_pct_per_market * bankroll_cents - market_exposure_cents
    if market_room <= 0:
        return 0
    stake = min(stake, market_room)

    # Correlation budget: legs of the same event are one bet wearing a
    # disguise. The incumbent counts them as separate positions against
    # MAX_ACTIVE_POSITIONS, which is how a "diversified" book ends up as
    # fifteen ways to lose the same game.
    cluster_room = limits.max_pct_per_cluster * bankroll_cents - cluster_exposure_cents
    if cluster_room <= 0:
        return 0
    stake = min(stake, cluster_room)

    gross_room = limits.max_gross_exposure_pct * bankroll_cents - gross_exposure_cents
    if gross_room <= 0:
        return 0
    stake = min(stake, gross_room)

    cash = bankroll_cents if available_cash_cents is None else available_cash_cents
    cash_room = cash - limits.cash_reserve_pct * bankroll_cents
    if cash_room <= 0:
        return 0
    stake = min(stake, cash_room)

    contracts = int(stake // (price_cents + f1))
    contracts = min(contracts, limits.max_contracts_per_order)
    if contracts < limits.min_contracts:
        return 0

    # Re-check EV at the size actually chosen, with the real per-order fee.
    f_actual = trade_fee_cents_per_contract(contracts, price_cents, is_maker, schedule)
    if p * 100.0 - price_cents - f_actual <= 0:
        return 0
    return contracts


def drawdown_multiplier(equity_cents: int, high_water_cents: int, ladder=None) -> float:
    """Size multiplier from a drawdown ladder.

    Same idea as the incumbent's -10/-15/-20 ladder, but continuous between
    rungs. A step function means one cent of drawdown can halve every
    position at once, which turns a bad hour into a forced deleveraging.
    """
    if ladder is None:
        ladder = [(0.10, 1.0), (0.20, 0.5), (0.30, 0.0)]
    if high_water_cents <= 0:
        return 1.0
    dd = max(0.0, (high_water_cents - equity_cents) / high_water_cents)

    prev_dd, prev_mult = 0.0, 1.0
    for lim, mult in ladder:
        if dd <= lim:
            if lim == prev_dd:
                return mult
            frac = (dd - prev_dd) / (lim - prev_dd)
            return prev_mult + (mult - prev_mult) * frac
        prev_dd, prev_mult = lim, mult
    return ladder[-1][1]
