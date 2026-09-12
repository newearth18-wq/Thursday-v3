"""The vocabulary of the trading module (§15, V14).

Small, frozen, and arithmetic-only. Nothing here reaches a broker, a price feed or a
network; these are the shapes the risk manager and the backtester pass between them.

One naming decision worth stating: a `Signal` is *what a strategy noticed*, and an `Order`
is *what may be sent*. They are separate types because the entire safety argument of this
module is that the second is only ever produced from the first by passing through the risk
manager. A strategy that could construct an `Order` directly would be a strategy that could
size its own position.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class Stage(StrEnum):
    """How far a strategy has been proven. Ordered, and the order is the whole point.

    Ordinal comparison is deliberate here and safe because `_ORDER` below is explicit —
    unlike the StrEnum comparisons `test_structural_audit_v86.py` forbids, which compare
    *strings* and once let a guest take CRITICAL actions because "HIGH" < "LOW".
    """

    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    LIMITED = "LIMITED"
    LIVE = "LIVE"


#: Explicit rank. Never `<` on the enum's string value.
_ORDER: dict[Stage, int] = {
    Stage.BACKTEST: 0,
    Stage.PAPER: 1,
    Stage.LIMITED: 2,
    Stage.LIVE: 3,
}


def rank(stage: Stage) -> int:
    return _ORDER[stage]


@dataclass(frozen=True)
class Bar:
    """One period of price. OHLC, and a volume nothing here uses yet."""

    when: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def __post_init__(self) -> None:
        if self.high < self.low:
            raise ValueError(f"bar at {self.when}: high {self.high} below low {self.low}")
        for name, value in (("open", self.open), ("close", self.close)):
            if not self.low <= value <= self.high:
                raise ValueError(
                    f"bar at {self.when}: {name} {value} outside the bar's {self.low}–{self.high}"
                )

    @property
    def day(self) -> date:
        return self.when.date()


@dataclass(frozen=True)
class Signal:
    """What a strategy noticed. It carries no size — sizing is the risk manager's job."""

    symbol: str
    side: Side
    when: datetime
    #: The price the strategy would act at.
    price: float
    #: Where the idea is wrong. Required: a signal with no stop cannot be sized, and a
    #: position whose loss has no floor is the one that ends an account.
    stop: float
    reason: str = ""

    def __post_init__(self) -> None:
        if self.price <= 0:
            raise ValueError(f"{self.symbol}: price {self.price} is not a price")
        if self.stop <= 0:
            raise ValueError(f"{self.symbol}: stop {self.stop} is not a price")
        if self.side is Side.BUY and self.stop >= self.price:
            raise ValueError(
                f"{self.symbol}: a long stop at {self.stop} is at or above the entry "
                f"{self.price} — that is not a stop, it is an exit"
            )
        if self.side is Side.SELL and self.stop <= self.price:
            raise ValueError(
                f"{self.symbol}: a short stop at {self.stop} is at or below the entry {self.price}"
            )

    @property
    def risk_per_unit(self) -> float:
        """Money lost per unit if the stop is hit. Always positive."""
        return abs(self.price - self.stop)


@dataclass(frozen=True)
class Order:
    """Sized, checked, and not yet sent anywhere.

    Only `RiskManager.size` produces one. There is no other constructor call in this
    package, and `tests/unit/test_trading_risk_v14.py` asserts it.
    """

    symbol: str
    side: Side
    quantity: float
    price: float
    stop: float
    stage: Stage
    #: Why the risk manager allowed this size. Shown to the owner before anything happens.
    rationale: str = ""

    @property
    def notional(self) -> float:
        return round(self.quantity * self.price, 2)

    @property
    def risk(self) -> float:
        """The most this order loses if the stop holds."""
        return round(self.quantity * abs(self.price - self.stop), 2)


@dataclass(frozen=True)
class Fill:
    """An order that was executed — on paper or in a backtest. Never live."""

    symbol: str
    side: Side
    quantity: float
    price: float
    when: datetime
    stage: Stage

    @property
    def notional(self) -> float:
        return round(self.quantity * self.price, 2)
