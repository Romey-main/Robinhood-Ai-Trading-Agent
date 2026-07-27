import random

import pytest

from arena.blend import MarketPriorBlender, PoolWeights, disagreement_penalty
from arena.calibration import logit, sigmoid
from arena.fees import trade_fee_cents_per_contract
from arena.kelly import (
    SizingLimits,
    drawdown_multiplier,
    kelly_fraction_binary,
    size_position,
    uncertainty_adjusted_p,
)


# -- pooling ---------------------------------------------------------------


def test_agreement_is_a_fixed_point():
    """The bug that cost $112/round: weights summing past 1 manufacture edge
    out of perfect agreement."""
    b = MarketPriorBlender()
    for p in (0.05, 0.2, 0.5, 0.8, 0.97):
        assert abs(b.blend(p, p) - p) < 1e-9


def test_agreement_stays_a_fixed_point_after_training():
    b = MarketPriorBlender()
    rng = random.Random(0)
    for _ in range(3000):
        q = rng.random()
        b.update(q, min(0.99, max(0.01, q + rng.gauss(0, 0.1))), 1 if rng.random() < q else 0)
    # s and b may drift, but a convex pool can never push agreement outward
    # by more than the learned sharpness/bias, which stays bounded.
    for p in (0.2, 0.5, 0.8):
        assert abs(b.blend(p, p) - p) < 0.12


def test_blend_lies_between_its_inputs_in_log_odds():
    b = MarketPriorBlender(w=PoolWeights(a=0.4, s=1.0, b=0.0))
    lo, hi = 0.2, 0.7
    out = b.blend(lo, hi)
    assert lo < out < hi


def test_signal_weight_decays_to_zero_when_signal_is_noise():
    """The regulariser must target 'trust the market', not the start value."""
    b = MarketPriorBlender()
    rng = random.Random(1)
    for _ in range(6000):
        q = rng.random()
        outcome = 1 if rng.random() < q else 0
        noise = min(0.99, max(0.01, rng.random()))
        b.update(q, noise, outcome)  # market = truth, signal = garbage
    assert b.w.a < 0.12
    assert b.signal_trust() < 0.12


def test_signal_weight_rises_when_signal_is_informative():
    b = MarketPriorBlender()
    rng = random.Random(2)
    for _ in range(6000):
        q = rng.random()
        outcome = 1 if rng.random() < q else 0
        biased_market = sigmoid(logit(q) + rng.gauss(0, 0.8))
        sharp_signal = sigmoid(logit(q) + rng.gauss(0, 0.2))
        b.update(biased_market, sharp_signal, outcome)
    assert b.w.a > 0.4


def test_blender_roundtrips():
    b = MarketPriorBlender()
    b.update(0.4, 0.6, 1)
    c = MarketPriorBlender.from_dict(b.to_dict())
    assert abs(c.blend(0.4, 0.6) - b.blend(0.4, 0.6)) < 1e-12


def test_disagreement_penalty_shrinks_with_gap():
    assert disagreement_penalty(0.5, 0.5) == 1.0
    assert disagreement_penalty(0.5, 0.6, 0.25) > disagreement_penalty(0.5, 0.9, 0.25)
    assert abs(disagreement_penalty(0.2, 0.45, 0.25) - 0.5) < 1e-9


# -- Kelly -----------------------------------------------------------------


def test_kelly_zero_when_no_edge():
    assert kelly_fraction_binary(0.5, 50.0, 0.0) == 0.0
    assert kelly_fraction_binary(0.4, 50.0, 0.0) == 0.0


def test_kelly_grows_with_edge():
    a = kelly_fraction_binary(0.55, 50.0)
    b = kelly_fraction_binary(0.70, 50.0)
    assert 0 < a < b


def test_fees_reduce_kelly():
    without = kelly_fraction_binary(0.60, 50.0, 0.0)
    with_fee = kelly_fraction_binary(0.60, 50.0, 1.75)
    assert with_fee < without


def test_fees_can_erase_a_thin_edge_entirely():
    assert kelly_fraction_binary(0.51, 50.0, 2.0) == 0.0


def test_uncertainty_lowers_the_probability_used():
    assert uncertainty_adjusted_p(0.7, 0.1, z=1.0) == pytest.approx(0.6)
    assert uncertainty_adjusted_p(0.7, 0.1, z=0.0) == pytest.approx(0.7)


def test_size_respects_per_market_cap():
    limits = SizingLimits(kelly_fraction=1.0, max_pct_per_market=0.02)
    n = size_position(0.9, 50.0, 100_000, limits)
    assert n * 50 <= 0.02 * 100_000 + 50


def test_size_counts_existing_exposure_in_the_market():
    """Applying the cap per order silently turns it into no cap at all."""
    limits = SizingLimits(kelly_fraction=1.0, max_pct_per_market=0.02)
    fresh = size_position(0.9, 50.0, 100_000, limits)
    loaded = size_position(0.9, 50.0, 100_000, limits, market_exposure_cents=2000)
    assert fresh > 0
    assert loaded == 0


def test_size_respects_cluster_and_gross_caps():
    limits = SizingLimits(kelly_fraction=1.0, max_pct_per_cluster=0.05, max_gross_exposure_pct=0.10)
    assert size_position(0.9, 50.0, 100_000, limits, cluster_exposure_cents=5000) == 0
    assert size_position(0.9, 50.0, 100_000, limits, gross_exposure_cents=10_000) == 0


def test_size_respects_cash_reserve():
    limits = SizingLimits(cash_reserve_pct=0.5)
    assert size_position(0.9, 50.0, 100_000, limits, available_cash_cents=40_000) == 0


def test_size_zero_when_edge_does_not_clear_fees():
    limits = SizingLimits()
    p = 50.5 / 100.0  # half a cent of edge, fee is ~1.75c
    assert size_position(p, 50.0, 1_000_000, limits) == 0


def test_size_never_returns_ev_negative_orders():
    rng = random.Random(0)
    limits = SizingLimits()
    for _ in range(500):
        price = rng.randint(2, 98)
        p = rng.random()
        n = size_position(p, price, 200_000, limits)
        if n:
            fee = trade_fee_cents_per_contract(n, price)
            assert p * 100 - price - fee > 0


def test_drawdown_multiplier_is_continuous():
    assert drawdown_multiplier(100, 100) == 1.0
    assert drawdown_multiplier(0, 100) == 0.0
    mid = drawdown_multiplier(85, 100)
    assert 0.0 < mid < 1.0
    # No cliff: a one-cent move must not halve the size.
    a = drawdown_multiplier(8999, 10000)
    b = drawdown_multiplier(9000, 10000)
    assert abs(a - b) < 0.02
