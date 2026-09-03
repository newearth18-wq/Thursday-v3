"""What Thursday may fix by itself, and what it may never (§59, V10).

The spec gives both lists, and the interesting thing is the shape of the boundary rather
than the entries:

    allowed        restart a worker · retry a safe network request · switch model
                   · switch agent · reconnect a node
    never          change security · install a system component · disable protection
                   · admin repair

Every allowed repair restores a capability the system already had. Every forbidden one
*changes what the system is permitted to do* — and a system that can widen its own
permissions to fix itself has no permission model, only a delay before it decides it needs
more. That is the distinction, and it is why the deny list is checked first and cannot be
reached around: a repair is refused because of what it *is*, not because of how urgent the
failure looked.

The second rule is quieter and matters as much. A recovery that repeats forever is not a
recovery: it is an outage with a busy loop, and it hides the failure from the person who
could actually fix it. So attempts are bounded per component, and exhausting them escalates
— which is the honest outcome, because at that point Thursday genuinely cannot fix this.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from thursday_core.logging import get_logger

log = get_logger(__name__)

#: Repairs Thursday may perform unattended. Each restores a capability that already existed;
#: none of them widens what the system may do.
SELF_REPAIRS: frozenset[str] = frozenset(
    {
        "restart_worker",
        "retry_request",
        "switch_model",
        "switch_agent",
        "reconnect_node",
        "clear_cache",
    }
)

#: Never automatically, whatever is broken and however obvious the fix looks. These change
#: what the system is *allowed* to do rather than restoring what it could already do.
NEVER_AUTOMATIC: frozenset[str] = frozenset(
    {
        "change_security",
        "change_permission",
        "disable_protection",
        "disable_audit",
        "install_component",
        "admin_repair",
        "rotate_credential",
        "grant_access",
    }
)

#: Attempts per component before Thursday stops and says so. A recovery that repeats for
#: ever is an outage with a busy loop, and it hides the failure from the one person who
#: could fix it.
MAX_ATTEMPTS = 3

#: After this long with no failure, a component's attempt count resets — otherwise a system
#: up for a month is one that used its three attempts in March and can never self-heal again.
ATTEMPT_WINDOW = timedelta(hours=1)


@dataclass(frozen=True)
class RepairOutcome:
    attempted: bool
    ok: bool
    action: str
    component: str
    reason: str

    def __bool__(self) -> bool:
        return self.ok


@dataclass
class _Attempts:
    count: int = 0
    last: datetime = field(default_factory=lambda: datetime.now(UTC))


def is_self_repairable(action: str) -> bool:
    """Whether this repair may be done unattended.

    The deny list is checked first and an unlisted action is refused. Fail-closed, because
    the failure mode of the other order is a repair nobody sanctioned running on the
    strength of not having been thought of.
    """
    name = action.strip().lower()
    if name in NEVER_AUTOMATIC:
        return False
    return name in SELF_REPAIRS


class SelfRecovery:
    """Performs the repairs on the allowed list, bounded, and escalates the rest."""

    def __init__(
        self,
        *,
        max_attempts: int = MAX_ATTEMPTS,
        window: timedelta = ATTEMPT_WINDOW,
    ) -> None:
        self._repairs: dict[str, Callable[[], Awaitable[Any] | Any]] = {}
        self._attempts: dict[str, _Attempts] = {}
        self._max = max_attempts
        self._window = window

    def register(self, action: str, repair: Callable[[], Awaitable[Any] | Any]) -> None:
        """Attach a repair. Refused here if it is not on the allowed list.

        Refused at *registration* rather than at call time on purpose: a forbidden repair
        that exists and is merely never invoked is one line away from being invoked, and
        the wiring is where a reviewer would look.
        """
        if not is_self_repairable(action):
            raise PermissionError(
                f"{action!r} may never be performed automatically — it changes what "
                "Thursday is permitted to do rather than restoring what it could already do"
            )
        self._repairs[action] = repair

    async def repair(
        self, component: str, action: str, *, now: datetime | None = None
    ) -> RepairOutcome:
        """Try to fix one component. Returns what happened, including "I did not try"."""
        now = now or datetime.now(UTC)

        if not is_self_repairable(action):
            log.warning("self_repair_refused", component=component, action=action)
            return RepairOutcome(
                attempted=False,
                ok=False,
                action=action,
                component=component,
                reason=(f"{action} is not something I may do on my own — it needs a person"),
            )

        attempts = self._attempts.get(component)
        if attempts is not None and now - attempts.last > self._window:
            # Long enough without trouble that the earlier failures are not this failure.
            attempts = None
        if attempts is not None and attempts.count >= self._max:
            return RepairOutcome(
                attempted=False,
                ok=False,
                action=action,
                component=component,
                reason=(
                    f"I have already tried to recover {component} {attempts.count} times; "
                    "something is wrong that I cannot fix"
                ),
            )

        repair = self._repairs.get(action)
        if repair is None:
            return RepairOutcome(
                attempted=False,
                ok=False,
                action=action,
                component=component,
                reason=f"no {action} repair is wired up",
            )

        record = self._attempts.setdefault(component, _Attempts(count=0, last=now))
        record.count += 1
        record.last = now

        try:
            result = repair()
            if hasattr(result, "__await__"):
                await result
        except Exception as exc:
            log.warning("self_repair_failed", component=component, action=action, error=str(exc))
            return RepairOutcome(
                attempted=True, ok=False, action=action, component=component, reason=str(exc)
            )

        log.info("self_repaired", component=component, action=action, attempt=record.count)
        return RepairOutcome(
            attempted=True,
            ok=True,
            action=action,
            component=component,
            reason=f"{action} succeeded on attempt {record.count}",
        )

    def clear(self, component: str) -> None:
        """Forget a component's attempts — called when it is healthy again."""
        self._attempts.pop(component, None)

    def exhausted(self) -> list[str]:
        """Components Thursday has given up on. These belong in the brief."""
        return [name for name, a in self._attempts.items() if a.count >= self._max]
