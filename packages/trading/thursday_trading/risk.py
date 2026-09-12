"""The risk manager, and the only way an order gets made (§15, V14).

The brief's rule is one sentence — *live execution must not bypass the Risk Manager* — and
the way to keep it is structural rather than procedural. `Order` is produced **here and
nowhere else**: a strategy emits a `Signal`, which carries no size, and only `size()` turns
one into something with a quantity on it. A strategy that could construct an `Order` would
be a strategy that could size its own position, and the rule would be a convention somebody
has to remember.

Everything below is arithmetic, and every refusal names the number that caused it. There is
no model anywhere in this package.

**This module does not decide whether a trade is a good idea.** It decides how much, and
whether the account may take another one at all. Those are different questions and only the
second has an arithmetic answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from thursday_trading.models import Order, Signal, Stage


class RiskRefused(ValueError):
    """The risk manager would not allow this. Never caught and retried inside this package."""


@dataclass(frozen=True)
class Limits:
    """The account's own rules. Every one of them is a number somebody chose.

    Defaults are conservative rather than typical, because a default that is merely typical
    becomes the setting nobody revisited.
    """

    #: Fraction of equity risked on one idea, measured to the stop — not the position size.
    risk_per_trade: float = 0.01
    #: Hard ceiling on one position's notional, as a fraction of equity.
    max_position: float = 0.20
    #: Realised loss in one day, as a fraction of starting equity, after which the day stops.
    max_daily_loss: float = 0.03
    #: Peak-to-trough fall, as a fraction of the high-water mark, after which everything stops.
    max_drawdown: float = 0.10
    #: How many positions may be open at once.
    max_positions: int = 5
    #: Smallest tradeable increment, in units. Zero means fractional sizes are allowed,
    #: which is true of crypto and FX and false of every stock exchange — the SET trades in
    #: board lots of 100. Left at zero by default because guessing a venue's lot is worse
    #: than admitting the account has not told us which venue it is.
    lot_size: float = 0.0

    def __post_init__(self) -> None:
        for name in ("risk_per_trade", "max_position", "max_daily_loss", "max_drawdown"):
            value = float(getattr(self, name))
            if not 0 < value <= 1:
                raise RiskRefused(f"{name} ต้องอยู่ระหว่าง 0 ถึง 1 — ได้ {value}")
        if self.max_positions < 1:
            raise RiskRefused(f"max_positions ต้องอย่างน้อย 1 — ได้ {self.max_positions}")
        if self.lot_size < 0:
            raise RiskRefused(f"lot_size ติดลบไม่ได้ — ได้ {self.lot_size}")


@dataclass
class Account:
    """Equity, and the memory a risk limit needs to mean anything.

    A drawdown cap without a high-water mark is not a cap; a daily loss cap without knowing
    what the day started at is not a cap either. Both are kept here rather than recomputed,
    so a restart cannot reset them by accident — `peak` only ever rises.
    """

    equity: float
    peak: float = 0.0
    day: date | None = None
    day_start_equity: float = 0.0
    open_positions: int = 0

    def __post_init__(self) -> None:
        if self.equity <= 0:
            raise RiskRefused(f"เงินทุน {self.equity} เป็นไปไม่ได้")
        self.peak = max(self.peak, self.equity)
        self.day_start_equity = self.day_start_equity or self.equity

    def mark(self, equity: float, when: date) -> None:
        """Update equity, rolling the day over when the date changes."""
        if self.day is None or when != self.day:
            self.day = when
            self.day_start_equity = self.equity
        self.equity = equity
        # Never falls. A high-water mark that drops turns a drawdown into a fresh start.
        self.peak = max(self.peak, equity)

    @property
    def drawdown(self) -> float:
        """Fraction below the high-water mark."""
        return 0.0 if self.peak <= 0 else max(0.0, (self.peak - self.equity) / self.peak)

    @property
    def day_loss(self) -> float:
        """Fraction lost since the day started. Zero on a winning day."""
        start = self.day_start_equity or self.equity
        return 0.0 if start <= 0 else max(0.0, (start - self.equity) / start)


@dataclass
class RiskManager:
    """Sizes orders, and stops the account when it should stop."""

    limits: Limits = field(default_factory=Limits)
    #: §69's emergency stop, for this module. Once on, nothing is sized until a person
    #: clears it — there is no timeout and no automatic reset, because a kill switch that
    #: turns itself back on is not a kill switch.
    halted: bool = False
    halt_reason: str = ""

    def halt(self, reason: str) -> None:
        self.halted = True
        self.halt_reason = reason

    def resume(self) -> None:
        """Cleared by a person, never by this module."""
        self.halted = False
        self.halt_reason = ""

    def check(self, account: Account) -> list[str]:
        """Every reason this account may not open a position now. Empty means it may."""
        blocks: list[str] = []
        if self.halted:
            blocks.append(f"หยุดการเทรดอยู่: {self.halt_reason or 'สั่งหยุดด้วยมือ'}")
        if account.drawdown >= self.limits.max_drawdown:
            blocks.append(
                f"ขาดทุนสะสมจากจุดสูงสุด {account.drawdown:.1%} ถึงเพดาน {self.limits.max_drawdown:.1%}"
            )
        if account.day_loss >= self.limits.max_daily_loss:
            blocks.append(
                f"ขาดทุนวันนี้ {account.day_loss:.1%} ถึงเพดาน {self.limits.max_daily_loss:.1%}"
            )
        if account.open_positions >= self.limits.max_positions:
            blocks.append(
                f"เปิดสถานะอยู่ {account.open_positions} รายการ ถึงเพดาน {self.limits.max_positions}"
            )
        return blocks

    def size(self, signal: Signal, account: Account, *, stage: Stage) -> Order:
        """Turn a signal into an order, or refuse with the number that stopped it.

        **The only place an `Order` is constructed.** Sizing is risk-first: the quantity
        comes from what the account is willing to lose if the stop is hit, then the position
        cap trims it. Deriving quantity from a target position size instead would make the
        loss whatever the stop distance happened to be that day.
        """
        blocks = self.check(account)
        if blocks:
            raise RiskRefused(" / ".join(blocks))

        risk_budget = account.equity * self.limits.risk_per_trade
        per_unit = signal.risk_per_unit
        if per_unit <= 0:  # pragma: no cover - Signal.__post_init__ forbids it
            raise RiskRefused(f"{signal.symbol}: ระยะ stop เป็นศูนย์ คำนวณขนาดไม่ได้")

        quantity = risk_budget / per_unit
        rationale = (
            f"เสี่ยง {self.limits.risk_per_trade:.1%} ของ {account.equity:,.2f} "
            f"= {risk_budget:,.2f} ÷ ระยะ stop {per_unit:,.4f}"
        )

        cap = account.equity * self.limits.max_position
        if quantity * signal.price > cap:
            quantity = cap / signal.price
            rationale += f" แล้วลดลงตามเพดานสถานะ {self.limits.max_position:.0%} ({cap:,.2f})"

        quantity = round(quantity, 6)
        if self.limits.lot_size > 0:
            # Down to a whole lot, never up: rounding up would put more on the line than
            # the risk figure above allows, which is the one number this method exists for.
            lots = int(quantity / self.limits.lot_size)
            whole = round(lots * self.limits.lot_size, 6)
            if whole <= 0:
                raise RiskRefused(
                    f"{signal.symbol}: ขนาดที่คำนวณได้ {quantity:,.4f} หน่วย "
                    f"ไม่ถึงขั้นต่ำ {self.limits.lot_size:,.0f} หน่วยต่อล็อต — "
                    f"เงินทุน {account.equity:,.2f} เล็กเกินไปสำหรับ stop กว้าง {per_unit:,.4f}"
                )
            if whole < quantity:
                rationale += f" แล้วปัดลงเป็นล็อตละ {self.limits.lot_size:,.0f} ({whole:,.0f} หน่วย)"
            quantity = whole
        if quantity <= 0:
            raise RiskRefused(
                f"{signal.symbol}: ขนาดที่คำนวณได้เป็นศูนย์ — "
                f"เงินทุน {account.equity:,.2f} เล็กเกินกว่าจะรับ stop กว้าง {per_unit:,.4f}"
            )

        return Order(
            symbol=signal.symbol,
            side=signal.side,
            quantity=quantity,
            price=signal.price,
            stop=signal.stop,
            stage=stage,
            rationale=rationale,
        )

    def after_fill(self, account: Account, equity: float, when: date) -> list[str]:
        """Mark the account and halt it if a cap has been breached.

        Called after every fill rather than before the next signal, because a cap checked
        only on the way in lets a position that is already open take the account past it.
        """
        account.mark(equity, when)
        breaches: list[str] = []
        if account.drawdown >= self.limits.max_drawdown:
            breaches.append(f"ขาดทุนสะสม {account.drawdown:.1%}")
        if account.day_loss >= self.limits.max_daily_loss:
            breaches.append(f"ขาดทุนวันนี้ {account.day_loss:.1%}")
        if breaches and not self.halted:
            self.halt(" และ ".join(breaches))
        return breaches

    def to_dict(self) -> dict[str, Any]:
        return {
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "limits": {
                "risk_per_trade": self.limits.risk_per_trade,
                "max_position": self.limits.max_position,
                "max_daily_loss": self.limits.max_daily_loss,
                "max_drawdown": self.limits.max_drawdown,
                "max_positions": self.limits.max_positions,
                "lot_size": self.limits.lot_size,
            },
        }
