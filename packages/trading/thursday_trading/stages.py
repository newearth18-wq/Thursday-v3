"""Stage progression: backtest → paper → limited → live (§15, V14).

The brief requires the ladder. What it does not say, and what this module decides, is **what
counts as having passed a rung** — and the answer here is deliberately not "it made money".

A profit threshold would be this project inventing a standard nobody adopted, and worse, it
would be a standard that rewards the one thing a backtest is best at producing: a curve
fitted to the past. So a stage records *evidence* — how many trades, over what period, with
what result — and promoting to the next rung is the **owner's decision**, taken with those
figures in front of them. Thursday proposes; the owner decides (§8).

What the gate does enforce is that the rungs happen in order and that each one actually ran.
A strategy cannot reach LIVE without a recorded LIMITED, which needs a recorded PAPER, which
needs a recorded BACKTEST — and none of those can be recorded without trades to show for it.

**There is no live broker in this repository.** See `ports.py`. The ladder is built so that
when one exists it cannot be reached by accident; today the top rung has nothing behind it,
and that is stated rather than implied.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from thursday_trading.models import Stage, rank


class StageRefused(ValueError):
    """A promotion or a run that the ladder does not allow."""


#: Below this many trades, a stage's numbers are not evidence of anything. Not a quality
#: bar — a sample-size floor, which is a different and much weaker claim.
MIN_TRADES = 20


@dataclass(frozen=True)
class StageResult:
    """What happened on one rung. Facts, not a verdict."""

    stage: Stage
    trades: int
    #: Realised profit or loss over the run, in account currency. May be negative.
    pnl: float
    #: Worst peak-to-trough fall observed during the run, as a fraction.
    max_drawdown: float
    started: date
    finished: date

    def __post_init__(self) -> None:
        if self.trades < 0:
            raise StageRefused(f"จำนวนการเทรด {self.trades} เป็นไปไม่ได้")
        if self.finished < self.started:
            raise StageRefused(f"วันจบ {self.finished} ก่อนวันเริ่ม {self.started}")

    @property
    def days(self) -> int:
        return (self.finished - self.started).days

    @property
    def enough_trades(self) -> bool:
        return self.trades >= MIN_TRADES

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": str(self.stage),
            "trades": self.trades,
            "pnl": round(self.pnl, 2),
            "max_drawdown": round(self.max_drawdown, 4),
            "days": self.days,
            "enough_trades": self.enough_trades,
            # Said in the data, not only in the prose: a recorded stage is a stage that ran.
            "verdict": "ran",
        }


@dataclass
class Progression:
    """One strategy's position on the ladder."""

    strategy: str
    results: dict[Stage, StageResult] = field(default_factory=dict)
    #: Stages the owner has explicitly approved moving up to. Never set by this module.
    approved: set[Stage] = field(default_factory=set)

    @property
    def stage(self) -> Stage:
        """The highest rung this strategy may currently run at."""
        current = Stage.BACKTEST
        for candidate in (Stage.PAPER, Stage.LIMITED, Stage.LIVE):
            if not self.blockers(candidate):
                current = candidate
        return current

    def record(self, result: StageResult) -> None:
        """Store what a run produced. Recording is not promoting."""
        if not self.blockers(result.stage) or result.stage is Stage.BACKTEST:
            self.results[result.stage] = result
            return
        raise StageRefused(
            f"{self.strategy}: ยังรัน {result.stage} ไม่ได้ — "
            + " / ".join(self.blockers(result.stage))
        )

    def approve(self, stage: Stage) -> None:
        """The owner's decision, recorded. The only way `approved` ever grows."""
        self.approved.add(stage)

    def blockers(self, stage: Stage) -> list[str]:
        """Every reason this strategy may not run at `stage`. Empty means it may."""
        if stage is Stage.BACKTEST:
            return []

        reasons: list[str] = []
        previous = _previous(stage)
        result = self.results.get(previous)

        if result is None:
            reasons.append(f"ยังไม่ได้รัน {previous}")
        elif not result.enough_trades:
            reasons.append(f"{previous} มีเพียง {result.trades} รายการ ต้องอย่างน้อย {MIN_TRADES}")

        if stage not in self.approved:
            # Not a formality. Every rung above backtest risks something real at the top of
            # the ladder, and the decision to climb is the owner's rather than a threshold's.
            reasons.append(f"เจ้าของยังไม่อนุมัติให้ขึ้น {stage}")

        return reasons

    def may_run(self, stage: Stage) -> bool:
        return not self.blockers(stage)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "stage": str(self.stage),
            "approved": sorted(str(s) for s in self.approved),
            "results": {str(k): v.to_dict() for k, v in self.results.items()},
            "blockers": {
                str(s): self.blockers(s) for s in (Stage.PAPER, Stage.LIMITED, Stage.LIVE)
            },
        }


def _previous(stage: Stage) -> Stage:
    ordered = sorted(Stage, key=rank)
    return ordered[rank(stage) - 1]
