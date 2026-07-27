"""Core data types shared by feeds, bots, the simulator and the scorer."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Side(str, Enum):
    YES = "yes"
    NO = "no"

    @property
    def other(self) -> "Side":
        return Side.NO if self is Side.YES else Side.YES


class OrderStyle(str, Enum):
    """How an order reaches the book."""

    TAKE = "take"  # cross the spread, immediate fill, taker fee
    MAKE = "make"  # rest on the book, may not fill, maker fee


@dataclass(frozen=True)
class MarketSnapshot:
    """One market at one instant, as a bot sees it.

    Prices are integer cents on the YES side, as Kalshi quotes them. The NO
    side is derived: no_bid = 100 - yes_ask, no_ask = 100 - yes_bid. Carrying
    a single source of truth for the book stops the two sides drifting apart,
    which is a classic source of phantom arbitrage in prediction-market bots.
    """

    ticker: str
    series: str
    t: int  # discrete tick index within the round
    yes_bid: int
    yes_ask: int
    yes_bid_size: int
    yes_ask_size: int
    volume: int
    ticks_to_close: int
    # Ground truth / labels. `p_true` is only populated by simulated feeds and
    # must never be read by a bot -- the simulator uses it to resolve.
    p_true: Optional[float] = None
    outcome: Optional[int] = None  # 1 = YES resolved true, 0 = NO
    # Cluster key for correlation-aware risk (typically the event ticker: all
    # legs of one game move together).
    cluster: str = ""
    title: str = ""

    @property
    def no_bid(self) -> int:
        return 100 - self.yes_ask

    @property
    def no_ask(self) -> int:
        return 100 - self.yes_bid

    @property
    def mid(self) -> float:
        return (self.yes_bid + self.yes_ask) / 2.0

    @property
    def spread(self) -> int:
        return self.yes_ask - self.yes_bid

    @property
    def two_sided(self) -> bool:
        return self.yes_bid > 0 and self.yes_ask < 100 and self.yes_ask > self.yes_bid

    def ask_for(self, side: Side) -> int:
        """Price paid to buy `side` immediately."""
        return self.yes_ask if side is Side.YES else self.no_ask

    def bid_for(self, side: Side) -> int:
        """Price received to sell `side` immediately."""
        return self.yes_bid if side is Side.YES else self.no_bid

    def size_at_ask(self, side: Side) -> int:
        return self.yes_ask_size if side is Side.YES else self.yes_bid_size


@dataclass(frozen=True)
class Signal:
    """A bot's private read on a market, before it decides anything.

    Kept separate from the order so the scorer can grade *forecasting* apart
    from *trading*. A bot can be a good forecaster and a bad trader; without
    this split you can never tell which half is losing the money.
    """

    ticker: str
    p_signal: float  # raw model estimate, pre-blend
    p_final: float  # what the bot actually believes after blending/calibration
    confidence: float  # 0..1, drives sizing shrinkage
    source: str = ""


@dataclass
class Order:
    ticker: str
    side: Side
    contracts: int
    limit_cents: int
    style: OrderStyle
    p_est: float
    reason: str = ""
    # Set when the order is a close of an existing position.
    is_exit: bool = False
    # Ticks the order may rest before the bot gives up on it.
    ttl_ticks: int = 1


@dataclass
class Fill:
    ticker: str
    side: Side
    contracts: int
    price_cents: int
    fee_cents: int
    is_maker: bool
    t: int
    is_exit: bool = False
    p_est: float = 0.5
    reason: str = ""

    @property
    def cost_cents(self) -> int:
        """Cash out the door: premium plus fee."""
        return self.contracts * self.price_cents + self.fee_cents


@dataclass
class Position:
    ticker: str
    side: Side
    contracts: int = 0
    cost_basis_cents: int = 0  # premium paid, excluding fees
    fees_paid_cents: int = 0
    cluster: str = ""
    p_est_at_entry: float = 0.5
    entry_tick: int = 0

    @property
    def avg_price(self) -> float:
        return self.cost_basis_cents / self.contracts if self.contracts else 0.0


@dataclass
class Resolution:
    """Outcome of a market, delivered once it closes."""

    ticker: str
    outcome: int  # 1 = YES
    p_true: Optional[float] = None
    final_market_price: Optional[float] = None
    cluster: str = ""
    series: str = ""


@dataclass
class RoundResult:
    """Everything one bot did in one round, for scoring and learning."""

    bot: str
    round_index: int
    start_bankroll_cents: int
    end_bankroll_cents: int
    fills: list = field(default_factory=list)
    forecasts: list = field(default_factory=list)  # (ticker, p_final, p_market, outcome)
    equity_curve: list = field(default_factory=list)
    params: dict = field(default_factory=dict)
    blocked: int = 0
    skipped_reasons: dict = field(default_factory=dict)

    @property
    def pnl_cents(self) -> int:
        return self.end_bankroll_cents - self.start_bankroll_cents
