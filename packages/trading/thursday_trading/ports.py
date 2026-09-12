"""Where a broker would go, and why nothing is there (§15, V14).

`Broker` is the port. `PaperBroker` fills against real bars with no money involved.

**There is no live adapter in this repository, and that is the design rather than a gap.**
The brief's rule is that live execution must not bypass the risk manager; the strongest way
to hold it is the one `communication.py` already uses for outbound messages — *no code path
to a sent one*. A live broker is a few hundred lines and an API key, and the moment it
exists the thing standing between a bug and somebody's money is a conditional. Today it is
the absence of an implementation, which no bug can get past.

`NoLiveBroker` is what `Stage.LIVE` resolves to. It satisfies the port and refuses every
order with the reason, so the ladder's top rung is reachable in code, testable, and
connected to nothing.

**Nothing in this package promises profitability**, and a backtest result is a description
of what those rules did on that data. It is not a forecast, and `StageResult.verdict` says
"ran" rather than "passed" for exactly that reason.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from thursday_trading.models import Bar, Fill, Order, Side, Stage


class BrokerRefused(RuntimeError):
    """An order a broker would not accept."""


@runtime_checkable
class Broker(Protocol):
    name: str
    live: bool

    def execute(self, order: Order, bar: Bar) -> Fill: ...


class PaperBroker:
    """Fills at the bar, with slippage, and moves no money.

    The fill price is the bar's open rather than its close: a signal computed from a closed
    bar can only be acted on in the next one, and filling at the close of the bar that
    produced the signal is the commonest way a backtest reports a return nobody could have
    had.
    """

    name = "paper"
    live = False

    def __init__(self, *, slippage: float = 0.0005) -> None:
        if slippage < 0:
            raise BrokerRefused(f"slippage {slippage} ติดลบ")
        self.slippage = slippage

    def execute(self, order: Order, bar: Bar) -> Fill:
        if order.quantity <= 0:
            raise BrokerRefused(f"{order.symbol}: จำนวน {order.quantity} เป็นไปไม่ได้")
        # Slippage always works against the order.
        drift = 1 + self.slippage if order.side is Side.BUY else 1 - self.slippage
        price = round(bar.open * drift, 6)
        return Fill(
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=price,
            when=bar.when,
            stage=order.stage,
        )


class NoLiveBroker:
    """What `Stage.LIVE` resolves to: a port with nothing behind it."""

    name = "none"
    live = False

    #: Said once, here, so the refusal is identical everywhere it is raised.
    REASON = (
        "Thursday ไม่มีช่องทางส่งคำสั่งซื้อขายจริง — โมดูลนี้มีแต่ backtest และ paper "
        "trading เท่านั้น และไม่มีโค้ดที่เชื่อมกับโบรกเกอร์จริงอยู่เลย "
        "(ดู packages/trading/thursday_trading/ports.py)"
    )

    def execute(self, order: Order, bar: Bar) -> Fill:
        raise BrokerRefused(self.REASON)


def broker_for(stage: Stage, *, slippage: float = 0.0005) -> Broker:
    """The broker a stage runs on.

    LIMITED is paper too. The brief describes it as a small live test, and a small live test
    needs the adapter that does not exist — so it runs on paper and says so, rather than
    being a rung that silently means the same as the one below it.
    """
    if stage is Stage.LIVE:
        return NoLiveBroker()
    return PaperBroker(slippage=slippage)
