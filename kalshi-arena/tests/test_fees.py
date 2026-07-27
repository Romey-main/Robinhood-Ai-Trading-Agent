import math

from arena.fees import (
    FeeSchedule,
    breakeven_prob,
    max_profitable_price,
    net_edge_cents,
    round_trip_fee_cents,
    smooth_fee_cents_per_contract,
    trade_fee_cents,
    trade_fee_cents_per_contract,
)


def test_fee_matches_published_formula_before_rounding():
    # 0.07 * C * P * (1-P), C=100 at 50c -> $1.75 -> 175c exactly, no rounding.
    assert trade_fee_cents(100, 50) == 175


def test_fee_rounds_up_per_order():
    # 1 contract at 50c: 0.07 * 1 * .5 * .5 = $0.0175 -> 2c, not 1c and not 1.75c.
    assert trade_fee_cents(1, 50) == 2
    assert math.isclose(smooth_fee_cents_per_contract(50), 1.75)


def test_rounding_makes_small_orders_relatively_more_expensive():
    small = trade_fee_cents_per_contract(1, 50)
    large = trade_fee_cents_per_contract(500, 50)
    assert small > large
    assert small == 2.0


def test_no_fee_at_certainty():
    assert trade_fee_cents(10, 0) == 0
    assert trade_fee_cents(10, 100) == 0


def test_fee_is_symmetric_around_fifty():
    assert trade_fee_cents(50, 30) == trade_fee_cents(50, 70)


def test_maker_rate_is_used_when_flagged():
    sched = FeeSchedule(taker_rate=0.07, maker_rate=0.01)
    assert trade_fee_cents(100, 50, is_maker=True, schedule=sched) < trade_fee_cents(
        100, 50, is_maker=False, schedule=sched
    )


def test_breakeven_prob_exceeds_naive_price():
    # Fees mean you need more than price/100 to break even.
    assert breakeven_prob(50, contracts=1) > 0.50
    assert breakeven_prob(50, contracts=1000) > 0.50


def test_net_edge_is_zero_at_breakeven():
    p = breakeven_prob(37, contracts=10)
    assert abs(net_edge_cents(p, 37, contracts=10)) < 0.51


def test_round_trip_costs_more_than_holding():
    hold = trade_fee_cents(20, 60)
    trip = round_trip_fee_cents(20, 60, 65)
    assert trip > hold


def test_max_profitable_price_is_below_fair_value():
    p = 0.62
    cap = max_profitable_price(p, contracts=10)
    assert cap < p * 100
    assert net_edge_cents(p, cap, contracts=10) >= -0.01
