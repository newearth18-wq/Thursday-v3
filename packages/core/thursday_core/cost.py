"""What Thursday spends, and the ceiling it will not cross (§61, Sprint 45).

Per-task budgets were already enforced, and they are not cost control. Two reasons:

**They only bind one task.** A budget of fifty cents stops one runaway task and does nothing
about five hundred small ones. Nobody sets out to spend a hundred dollars; they spend it
forty cents at a time over a fortnight, and every individual charge looked reasonable.

**They only saw the agents.** Spend was counted where an agent called `think`, which missed
the two model calls every single turn makes — the reasoning pass that interprets the
utterance and the supervisor pass that verifies the result. A system whose ordinary
conversation is invisible to its own accounting reports zero and means nothing by it.

So metering happens at the **router**, because that is the one place every model call passes
through, the same argument that puts authorization in one engine. A caller cannot opt out by
not reporting, because reporting is not something a caller does.

The rule that matters most here is what a cap does when it binds:

    a ceiling that stops Thursday working is worse than the overspend it prevents

Reaching the cap routes to the local model, which is free, and says so. It does not refuse
the work. Refusing would make the cap a self-inflicted outage, and an outage the owner
cannot distinguish from a broken assistant is one they will fix by removing the cap. Only
when there is no local model to fall back to does the cap refuse, and then it says exactly
that rather than failing as a model error.

A cap is the owner's, and nothing else may raise it — not an agent that finds it
inconvenient, not a model asked whether it should continue, not a document that says to.
There is no method here that widens a limit.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from thursday_core.logging import get_logger

log = get_logger(__name__)

#: Spend at or above this share of a cap is worth telling the owner about, while they can
#: still do something other than watch it stop. A warning that arrives with the refusal is
#: not a warning.
WARN_AT = 0.8

#: How long a charge stays in the ledger. Long enough for "what did last month cost", short
#: enough that the ledger of an assistant running for years is not unbounded.
RETENTION = timedelta(days=90)


@dataclass(frozen=True)
class Charge:
    """One model call, after the fact. Public accounting, no payload."""

    at: datetime
    provider: str
    tier: str
    tokens_in: int
    tokens_out: int
    usd: float
    task_id: UUID | None = None
    agent: str = ""

    @property
    def tokens(self) -> int:
        return self.tokens_in + self.tokens_out


@dataclass(frozen=True)
class CapVerdict:
    """Whether a call may go to a paid provider, and why not if not."""

    allowed: bool
    reason: str = ""
    #: Which cap bound — "daily" or "monthly". Empty when nothing bound.
    period: str = ""
    spent: float = 0.0
    cap: float | None = None

    def __bool__(self) -> bool:
        return self.allowed


@dataclass
class CostMeter:
    """The ledger, and the two ceilings over it.

    Local inference is recorded but never counted against a cap: it costs nothing, and a cap
    that throttled the free fallback would remove the very thing the cap falls back to.
    """

    daily_usd: float | None = None
    monthly_usd: float | None = None
    retention: timedelta = RETENTION
    _charges: list[Charge] = field(default_factory=list)
    _warned: set[str] = field(default_factory=set)

    # ------------------------------------------------------------------ recording

    def record(
        self,
        *,
        provider: str,
        tier: str,
        tokens_in: int = 0,
        tokens_out: int = 0,
        usd: float = 0.0,
        task_id: UUID | None = None,
        agent: str = "",
        now: datetime | None = None,
    ) -> Charge:
        """Note what a call cost. Called by the router, for every call, without exception."""
        charge = Charge(
            at=now or datetime.now(UTC),
            provider=provider,
            tier=tier,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            usd=usd,
            task_id=task_id,
            agent=agent,
        )
        self._charges.append(charge)
        self._prune(charge.at)
        return charge

    # ------------------------------------------------------------------ the ceiling

    def check(self, *, now: datetime | None = None) -> CapVerdict:
        """Whether a paid call may proceed. Asked *before* the call.

        Before, because after is an accounting record of money already gone. The per-task
        budget is checked after a charge because it bounds a task that is already running;
        a spending ceiling has to bound the next call or it bounds nothing.
        """
        now = now or datetime.now(UTC)
        for period, cap, spent in (
            ("daily", self.daily_usd, self.spent_today(now=now)),
            ("monthly", self.monthly_usd, self.spent_this_month(now=now)),
        ):
            if cap is None:
                continue
            if spent >= cap:
                return CapVerdict(
                    allowed=False,
                    reason=(
                        f"the {period} spending cap of ${cap:.2f} is reached (${spent:.2f} so far)"
                    ),
                    period=period,
                    spent=spent,
                    cap=cap,
                )
        return CapVerdict(allowed=True)

    def warnings(self, *, now: datetime | None = None) -> list[str]:
        """Caps close enough to bother the owner about, each said once per period.

        Once per period because a warning repeated on every turn is a warning nobody reads,
        and the one that matters is the first.
        """
        now = now or datetime.now(UTC)
        said: list[str] = []
        for period, cap, spent, key in (
            ("daily", self.daily_usd, self.spent_today(now=now), f"daily:{now.date()}"),
            (
                "monthly",
                self.monthly_usd,
                self.spent_this_month(now=now),
                f"monthly:{now:%Y-%m}",
            ),
        ):
            if cap is None or spent < cap * WARN_AT or key in self._warned:
                continue
            self._warned.add(key)
            said.append(
                f"ใช้งบ{'รายวัน' if period == 'daily' else 'รายเดือน'}ไปแล้ว "
                f"${spent:.2f} จาก ${cap:.2f} ครับ"
            )
        return said

    # ------------------------------------------------------------------ reading it back

    def spent_today(self, *, now: datetime | None = None) -> float:
        now = now or datetime.now(UTC)
        return self.spent(since=datetime.combine(now.date(), datetime.min.time(), tzinfo=UTC))

    def spent_this_month(self, *, now: datetime | None = None) -> float:
        now = now or datetime.now(UTC)
        first = datetime(now.year, now.month, 1, tzinfo=UTC)
        return self.spent(since=first)

    def spent(self, *, since: datetime | None = None, task_id: UUID | None = None) -> float:
        return sum(
            c.usd
            for c in self._charges
            if (since is None or c.at >= since) and (task_id is None or c.task_id == task_id)
        )

    def by_provider(self, *, since: datetime | None = None) -> dict[str, float]:
        totals: dict[str, float] = defaultdict(float)
        for charge in self._charges:
            if since is None or charge.at >= since:
                totals[charge.provider] += charge.usd
        return dict(totals)

    def by_day(self, *, days: int = 30, now: datetime | None = None) -> dict[date, float]:
        now = now or datetime.now(UTC)
        cutoff = now - timedelta(days=days)
        totals: dict[date, float] = defaultdict(float)
        for charge in self._charges:
            if charge.at >= cutoff:
                totals[charge.at.date()] += charge.usd
        return dict(totals)

    def tokens(self, *, since: datetime | None = None) -> int:
        return sum(c.tokens for c in self._charges if since is None or c.at >= since)

    def charges(self, *, limit: int = 100) -> list[Charge]:
        return self._charges[-limit:]

    def summary(self, *, now: datetime | None = None) -> dict:
        """What the cost dashboard and the brief both read (§133)."""
        now = now or datetime.now(UTC)
        today = self.spent_today(now=now)
        month = self.spent_this_month(now=now)
        return {
            "today_usd": round(today, 4),
            "month_usd": round(month, 4),
            "daily_cap_usd": self.daily_usd,
            "monthly_cap_usd": self.monthly_usd,
            "calls": len(self._charges),
            "tokens": self.tokens(),
            "by_provider": {k: round(v, 4) for k, v in self.by_provider().items()},
            "capped": not self.check(now=now).allowed,
        }

    # ------------------------------------------------------------------ backup (Sprint 47)

    def export_state(self) -> list[dict]:
        return [
            {
                "at": c.at.isoformat(),
                "provider": c.provider,
                "tier": c.tier,
                "tokens_in": c.tokens_in,
                "tokens_out": c.tokens_out,
                "usd": c.usd,
                "task_id": str(c.task_id) if c.task_id else None,
                "agent": c.agent,
            }
            for c in self._charges
        ]

    def import_state(self, rows: list[dict], *, replace: bool = True) -> int:
        """Restore the ledger, which is what makes a period cap survive a restart.

        Named as the fix for Sprint 45's stated gap: with the ledger only in memory, a
        restart reset the daily total, so restarting was a way around the cap.
        """
        from uuid import UUID

        if replace:
            self._charges.clear()
        for row in rows:
            self._charges.append(
                Charge(
                    at=datetime.fromisoformat(row["at"]),
                    provider=row.get("provider", ""),
                    tier=row.get("tier", ""),
                    tokens_in=int(row.get("tokens_in", 0)),
                    tokens_out=int(row.get("tokens_out", 0)),
                    usd=float(row.get("usd", 0.0)),
                    task_id=UUID(row["task_id"]) if row.get("task_id") else None,
                    agent=row.get("agent", ""),
                )
            )
        self._charges.sort(key=lambda c: c.at)
        return len(rows)

    # ------------------------------------------------------------------ internals

    def _prune(self, now: datetime) -> None:
        cutoff = now - self.retention
        if self._charges and self._charges[0].at < cutoff:
            self._charges = [c for c in self._charges if c.at >= cutoff]
