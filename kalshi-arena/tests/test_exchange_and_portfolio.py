import pytest

from arena.exchange_sim import ExchangeConfig, PaperExchange
from arena.fees import trade_fee_cents
from arena.portfolio import Portfolio
from arena.types import MarketSnapshot, Order, OrderStyle, Resolution, Side


def snap(ticker="M1", bid=40, ask=44, bsz=50, asz=50, vol=1000, ttc=10, t=0):
    return MarketSnapshot(
        ticker=ticker, series="S", t=t, yes_bid=bid, yes_ask=ask,
        yes_bid_size=bsz, yes_ask_size=asz, volume=vol,
        ticks_to_close=ttc, cluster="E1",
    )


def fresh(cash=100_000, seed=0):
    pf = Portfolio(cash_cents=cash, start_cents=cash)
    return pf, PaperExchange(pf, ExchangeConfig(), seed=seed)


# -- snapshot algebra ------------------------------------------------------


def test_no_side_is_derived_from_yes_side():
    s = snap(bid=40, ask=44)
    assert s.no_bid == 56 and s.no_ask == 60
    assert s.ask_for(Side.NO) == 60
    assert s.bid_for(Side.NO) == 56


def test_buying_both_sides_costs_more_than_a_dollar_when_spread_is_positive():
    s = snap(bid=40, ask=44)
    assert s.ask_for(Side.YES) + s.ask_for(Side.NO) == 104


# -- taker -----------------------------------------------------------------


def test_take_fills_at_the_offer_and_pays_the_taker_fee():
    pf, ex = fresh()
    s = snap()
    fills = ex.submit(
        [Order("M1", Side.YES, 10, 44, OrderStyle.TAKE, 0.6)], {"M1": s}, 0
    )
    assert len(fills) == 1
    f = fills[0]
    assert f.price_cents == 44 and not f.is_maker
    assert f.fee_cents == trade_fee_cents(10, 44, is_maker=False)
    assert pf.cash_cents == 100_000 - (10 * 44 + f.fee_cents)


def test_take_is_rejected_above_its_limit():
    pf, ex = fresh()
    fills = ex.submit([Order("M1", Side.YES, 5, 43, OrderStyle.TAKE, 0.6)], {"M1": snap()}, 0)
    assert fills == []
    assert pf.cash_cents == 100_000


def test_take_larger_than_displayed_size_pays_up():
    pf, ex = fresh()
    s = snap(asz=3)
    fills = ex.submit([Order("M1", Side.YES, 20, 46, OrderStyle.TAKE, 0.9)], {"M1": s}, 0)
    assert fills[0].price_cents == 45  # walked the book by one tick


def test_take_is_capped_by_cash():
    pf, ex = fresh(cash=500)
    fills = ex.submit([Order("M1", Side.YES, 100, 44, OrderStyle.TAKE, 0.9)], {"M1": snap()}, 0)
    assert fills[0].contracts < 100
    assert pf.cash_cents >= 0


# -- maker -----------------------------------------------------------------


def test_resting_order_does_not_fill_immediately():
    pf, ex = fresh()
    ex.submit([Order("M1", Side.YES, 10, 41, OrderStyle.MAKE, 0.6, ttl_ticks=3)], {"M1": snap()}, 0)
    assert pf.fills == []
    assert ex.resting["M1"]


def test_resting_buy_certainly_fills_when_market_trades_through_it():
    """Adverse selection: you get filled exactly when you did not want to be."""
    pf, ex = fresh()
    ex.submit([Order("M1", Side.YES, 10, 41, OrderStyle.MAKE, 0.6, ttl_ticks=5)], {"M1": snap()}, 0)
    crashed = snap(bid=36, ask=40, t=1)  # ask fell below our resting bid
    fills = ex.match_resting({"M1": crashed}, 1)
    assert len(fills) == 1
    assert fills[0].is_maker and fills[0].price_cents == 41
    # We paid 41 for something the market now offers at 40.
    assert crashed.ask_for(Side.YES) < fills[0].price_cents


def test_resting_order_expires_unfilled():
    pf, ex = fresh()
    ex.submit([Order("M1", Side.YES, 10, 41, OrderStyle.MAKE, 0.6, ttl_ticks=2)], {"M1": snap()}, 0)
    quiet = snap(bid=41, ask=44, vol=0, t=5)
    assert ex.match_resting({"M1": quiet}, 5) == []
    assert "M1" not in ex.resting


def test_resting_order_behind_the_touch_never_fills():
    pf, ex = fresh()
    ex.submit([Order("M1", Side.YES, 10, 30, OrderStyle.MAKE, 0.6, ttl_ticks=5)], {"M1": snap()}, 0)
    up = snap(bid=45, ask=48, t=1)
    assert ex.match_resting({"M1": up}, 1) == []


def test_maker_fill_pays_the_maker_fee_at_the_limit():
    pf, ex = fresh()
    ex.submit([Order("M1", Side.YES, 10, 41, OrderStyle.MAKE, 0.6, ttl_ticks=5)], {"M1": snap()}, 0)
    fills = ex.match_resting({"M1": snap(bid=36, ask=40, t=1)}, 1)
    assert fills[0].fee_cents == trade_fee_cents(10, 41, is_maker=True)


# -- settlement ------------------------------------------------------------


def test_settlement_pays_a_dollar_on_the_winning_side():
    pf, ex = fresh()
    ex.submit([Order("M1", Side.YES, 10, 44, OrderStyle.TAKE, 0.6)], {"M1": snap()}, 0)
    cash_after_buy = pf.cash_cents
    ex.settle([Resolution("M1", outcome=1)])
    assert pf.cash_cents == cash_after_buy + 1000
    assert pf.positions == {}


def test_settlement_pays_nothing_on_the_losing_side():
    pf, ex = fresh()
    ex.submit([Order("M1", Side.YES, 10, 44, OrderStyle.TAKE, 0.6)], {"M1": snap()}, 0)
    cash_after_buy = pf.cash_cents
    ex.settle([Resolution("M1", outcome=0)])
    assert pf.cash_cents == cash_after_buy


def test_no_side_wins_when_outcome_is_no():
    pf, ex = fresh()
    ex.submit([Order("M1", Side.NO, 10, 60, OrderStyle.TAKE, 0.6)], {"M1": snap()}, 0)
    cash = pf.cash_cents
    ex.settle([Resolution("M1", outcome=0)])
    assert pf.cash_cents == cash + 1000


def test_holding_both_sides_locks_a_dollar_and_loses_the_spread():
    pf, ex = fresh()
    s = snap(bid=40, ask=44)
    ex.submit([Order("M1", Side.YES, 10, 44, OrderStyle.TAKE, 0.5)], {"M1": s}, 0)
    ex.submit([Order("M1", Side.NO, 10, 60, OrderStyle.TAKE, 0.5)], {"M1": s}, 0)
    start = 100_000
    ex.settle([Resolution("M1", outcome=1)])
    # Paid 104c for a guaranteed 100c, plus two fees. Must be a loss.
    assert pf.cash_cents < start
    assert pf.cash_cents == start - 40 - pf.fees_paid_cents


def test_settlement_is_idempotent():
    pf, ex = fresh()
    ex.submit([Order("M1", Side.YES, 10, 44, OrderStyle.TAKE, 0.6)], {"M1": snap()}, 0)
    ex.settle([Resolution("M1", outcome=1)])
    cash = pf.cash_cents
    ex.settle([Resolution("M1", outcome=1)])
    assert pf.cash_cents == cash


# -- exits -----------------------------------------------------------------


def test_exit_sells_into_the_bid_and_realises_pnl():
    pf, ex = fresh()
    ex.submit([Order("M1", Side.YES, 10, 44, OrderStyle.TAKE, 0.6)], {"M1": snap()}, 0)
    higher = snap(bid=60, ask=64, t=1)
    fills = ex.submit(
        [Order("M1", Side.YES, 10, 60, OrderStyle.TAKE, 0.7, is_exit=True)], {"M1": higher}, 1
    )
    assert fills[0].price_cents == 60
    assert pf.realized_pnl_cents > 0
    assert pf.positions == {}


def test_exit_cannot_sell_more_than_held():
    pf, ex = fresh()
    ex.submit([Order("M1", Side.YES, 5, 44, OrderStyle.TAKE, 0.6)], {"M1": snap()}, 0)
    fills = ex.submit(
        [Order("M1", Side.YES, 50, 40, OrderStyle.TAKE, 0.6, is_exit=True)],
        {"M1": snap(t=1)}, 1,
    )
    assert fills[0].contracts == 5


# -- portfolio -------------------------------------------------------------


def test_mark_to_market_uses_the_mid():
    pf, ex = fresh()
    ex.submit([Order("M1", Side.YES, 10, 44, OrderStyle.TAKE, 0.6)], {"M1": snap()}, 0)
    equity = pf.mark_to_market_cents({"M1": snap()})
    assert equity == pf.cash_cents + int(round(42.0 * 10))


def test_liquidation_value_is_below_mark():
    pf, ex = fresh()
    ex.submit([Order("M1", Side.YES, 10, 44, OrderStyle.TAKE, 0.6)], {"M1": snap()}, 0)
    books = {"M1": snap()}
    assert pf.liquidation_value_cents(books) < pf.mark_to_market_cents(books)


def test_cluster_exposure_aggregates():
    pf, ex = fresh()
    for tk in ("M1", "M2"):
        ex.submit([Order(tk, Side.YES, 10, 44, OrderStyle.TAKE, 0.6)],
                  {tk: snap(ticker=tk)}, 0)
    assert pf.cluster_exposure_cents("E1") == 880
