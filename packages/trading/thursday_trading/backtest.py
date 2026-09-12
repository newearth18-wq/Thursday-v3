"""The backtest engine: deterministic, and honest about what it is (§15, V14).

Same inputs, same result, every time — which is what makes the number arguable rather than
merely quoted. Everything is computed from the bars; no model is involved anywhere.

Three things it refuses to do, each of which is a way a backtest produces a return nobody
could have had:

**It does not fill on the bar that produced the signal.** A signal computed from a closed
bar can only be acted on in the next one. Filling at the close of the bar you just read is
the commonest form of look-ahead and it flatters every strategy.

**It does not let the risk manager be skipped.** Every order comes from `RiskManager.size`,
so the caps bind inside the backtest exactly as they would anywhere else — including
halting the run when the drawdown cap is hit, rather than trading on through the hole.

**It does not report a verdict.** `BacktestReport` carries what happened on that data. There
is no "passed", no score, and no claim about the future — see `docs/25-trading.md` and
ADR 0063 for why a profitability threshold is deliberately absent.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

from thursday_trading.models import Bar, Fill, Side, Signal, Stage
from thursday_trading.ports import Broker, BrokerRefused, broker_for
from thursday_trading.risk import Account, RiskManager, RiskRefused
from thursday_trading.stages import StageResult

#: A strategy sees the bars up to and including now, and returns a signal or nothing. It
#: never sees the future, because it is only ever handed a slice ending at the current bar.
Strategy = Callable[[list[Bar]], Signal | None]


@dataclass
class Trade:
    """One round trip, opened and (usually) closed."""

    symbol: str
    side: Side
    quantity: float
    entry: float
    entry_when: date
    stop: float
    exit: float | None = None
    exit_when: date | None = None
    exit_reason: str = ""

    @property
    def open(self) -> bool:
        return self.exit is None

    @property
    def pnl(self) -> float:
        if self.exit is None:
            return 0.0
        direction = 1 if self.side is Side.BUY else -1
        return round((self.exit - self.entry) * self.quantity * direction, 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": str(self.side),
            "quantity": self.quantity,
            "entry": self.entry,
            "exit": self.exit,
            "pnl": self.pnl,
            "reason": self.exit_reason,
        }


@dataclass
class BacktestReport:
    """What those rules did on that data. Not a forecast."""

    strategy: str
    symbol: str
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[tuple[date, float]] = field(default_factory=list)
    starting_equity: float = 0.0
    halted: str = ""
    bars: int = 0

    @property
    def closed(self) -> list[Trade]:
        return [t for t in self.trades if not t.open]

    @property
    def pnl(self) -> float:
        return round(sum(t.pnl for t in self.closed), 2)

    @property
    def final_equity(self) -> float:
        return self.equity_curve[-1][1] if self.equity_curve else self.starting_equity

    @property
    def wins(self) -> int:
        return sum(1 for t in self.closed if t.pnl > 0)

    @property
    def win_rate(self) -> float:
        return round(self.wins / len(self.closed) * 100, 2) if self.closed else 0.0

    @property
    def max_drawdown(self) -> float:
        """Worst peak-to-trough fall on the equity curve."""
        peak = self.starting_equity
        worst = 0.0
        for _, equity in self.equity_curve:
            peak = max(peak, equity)
            if peak > 0:
                worst = max(worst, (peak - equity) / peak)
        return round(worst, 4)

    @property
    def return_percent(self) -> float:
        if not self.starting_equity:
            return 0.0
        return round((self.final_equity - self.starting_equity) / self.starting_equity * 100, 2)

    def as_stage_result(self, stage: Stage) -> StageResult:
        days = self.equity_curve
        return StageResult(
            stage=stage,
            trades=len(self.closed),
            pnl=self.pnl,
            max_drawdown=self.max_drawdown,
            started=days[0][0] if days else date.today(),
            finished=days[-1][0] if days else date.today(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "symbol": self.symbol,
            "bars": self.bars,
            "count": len(self.closed),
            "items": [t.to_dict() for t in self.closed],
            "starting_equity": self.starting_equity,
            "final_equity": round(self.final_equity, 2),
            "pnl": self.pnl,
            "return_percent": self.return_percent,
            "win_rate": self.win_rate,
            "max_drawdown": self.max_drawdown,
            "halted": self.halted,
            # Stated in the payload so nothing downstream can read this as advice.
            "disclaimer": ("ผลลัพธ์นี้คือสิ่งที่กฎชุดนี้ทำกับข้อมูลชุดนี้ ไม่ใช่การคาดการณ์อนาคต และไม่ใช่คำแนะนำการลงทุน"),
        }


def run(
    strategy: Strategy,
    bars: list[Bar],
    *,
    name: str = "strategy",
    symbol: str = "",
    equity: float = 100_000.0,
    risk: RiskManager | None = None,
    stage: Stage = Stage.BACKTEST,
    broker: Broker | None = None,
) -> BacktestReport:
    """Walk the bars once, in order, filling on the bar after the signal."""
    if len(bars) < 2:
        raise ValueError("ต้องมีแท่งราคาอย่างน้อยสองแท่ง")

    manager = risk or RiskManager()
    account = Account(equity=equity)
    fills = broker or broker_for(stage)
    report = BacktestReport(strategy=name, symbol=symbol, starting_equity=equity, bars=len(bars))

    position: Trade | None = None
    pending: Signal | None = None

    # A manager that is already halted never reaches the sizing call below, so without this
    # the run would come back empty with nothing saying why — an empty report that reads as
    # "the strategy found nothing" rather than "trading is stopped".
    if manager.halted:
        report.halted = manager.halt_reason or "หยุดการเทรดอยู่"

    for index, bar in enumerate(bars):
        # 1. An open position is checked against its stop before anything else happens.
        if position is not None:
            hit = (
                bar.low <= position.stop if position.side is Side.BUY else bar.high >= position.stop
            )
            if hit:
                position.exit = position.stop
                position.exit_when = bar.day
                position.exit_reason = "stop"
                account.open_positions = max(0, account.open_positions - 1)
                equity += position.pnl
                position = None

        # 2. A signal from the *previous* bar fills on this one. Never the bar that made it,
        #    and never a later one: a signal is good for exactly one bar. Holding one until
        #    a position closes would act on it at a price the strategy never saw — the
        #    first version of this loop kept `pending` alive across bars and could fill a
        #    seven-bar-old idea thirty bars later.
        if pending is not None:
            if position is None:
                try:
                    order = manager.size(pending, account, stage=stage)
                    fill: Fill = fills.execute(order, bar)
                    position = Trade(
                        symbol=fill.symbol,
                        side=fill.side,
                        quantity=fill.quantity,
                        entry=fill.price,
                        entry_when=bar.day,
                        stop=order.stop,
                    )
                    report.trades.append(position)
                    account.open_positions += 1
                except (RiskRefused, BrokerRefused) as exc:
                    # Not an error in the run. The risk manager declining is the risk
                    # manager working, and the run continues with no position.
                    if manager.halted:
                        report.halted = str(exc)
            pending = None

        # 3. Mark to market, then let the caps look at the result.
        floating = 0.0
        if position is not None:
            direction = 1 if position.side is Side.BUY else -1
            floating = (bar.close - position.entry) * position.quantity * direction
        marked = round(equity + floating, 2)
        report.equity_curve.append((bar.day, marked))
        breaches = manager.after_fill(account, marked, bar.day)
        if breaches and not report.halted:
            report.halted = " และ ".join(breaches)

        # 4. Ask the strategy, having shown it only what it could have seen.
        if index < len(bars) - 1 and not manager.halted:
            pending = strategy(bars[: index + 1])
            # A strategy sees bars, not a ticker, so it has no symbol to put on the signal.
            # The run does. Without this the trade rows come back unlabelled while the
            # report header names the instrument — a table nobody can read back.
            if pending is not None and not pending.symbol and symbol:
                pending = replace(pending, symbol=symbol)

    # An open position at the end is closed at the last close, and labelled as such — a
    # report that silently leaves it open counts its unrealised gain as if it were real.
    if position is not None:
        position.exit = bars[-1].close
        position.exit_when = bars[-1].day
        position.exit_reason = "ปิดท้ายช่วงทดสอบ"

    return report
