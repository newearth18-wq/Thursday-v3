"""Action policy (PART 20, PART 21).

Policies are data so a user can override them — except the BLOCK set, which is code and has
no path to `AUTO` through conversation, configuration, or an agent's own reasoning.

Actions are namespaced (`file.read`, `system.process.stop`). The namespace does real work:
the table resolves an unknown action by walking up its prefixes, so a newly added
`file.compress` inherits `file.*`'s level and risk instead of falling through to the
fail-closed default. See ADR 0007.
"""

from __future__ import annotations

from dataclasses import dataclass

from thursday_core.logging import get_logger
from thursday_shared.actions import canonical, prefixes
from thursday_shared.enums import AutonomyLevel, PermissionLevel, PolicyDecision, RiskLevel

log = get_logger(__name__)


@dataclass(frozen=True)
class ActionPolicy:
    action: str
    level: PermissionLevel
    default: PolicyDecision
    risk: RiskLevel = RiskLevel.LOW
    reversible: bool = True
    #: Above this many affected objects, an AUTO action becomes ASK (blast-radius cap).
    bulk_threshold: int | None = None
    #: True when the action rewrites an existing artefact and a backup must be taken first.
    requires_backup: bool = False


#: Actions that are never permitted, whatever the user, agent, or grant says (PART 21).
HARD_BLOCKED: frozenset[str] = frozenset(
    {
        "security.disable",
        "security.antivirus.disable",
        "security.firewall.disable",
        "audit.disable",
        "audit.modify",
        "audit.delete",
        "credential.export",
        "vault.read_raw",
        "permission.policy.modify",
        "permission.self_grant",
        "approval.disable",
        "disk.format",
        "system.directory.delete",
    }
)

_DEFAULTS: tuple[ActionPolicy, ...] = (
    # ---------------------------------------------------------------- level 0: observe
    ActionPolicy("file.read", PermissionLevel.READ, PolicyDecision.AUTO),
    ActionPolicy("file.search", PermissionLevel.READ, PolicyDecision.AUTO),
    ActionPolicy("file.list", PermissionLevel.READ, PolicyDecision.AUTO),
    ActionPolicy("system.info", PermissionLevel.READ, PolicyDecision.AUTO),
    ActionPolicy("system.process.list", PermissionLevel.READ, PolicyDecision.AUTO),
    ActionPolicy("window.active", PermissionLevel.READ, PolicyDecision.AUTO),
    ActionPolicy("clipboard.read", PermissionLevel.READ, PolicyDecision.AUTO),
    ActionPolicy("audio.volume.get", PermissionLevel.READ, PolicyDecision.AUTO),
    ActionPolicy("memory.search", PermissionLevel.READ, PolicyDecision.AUTO),
    ActionPolicy("obsidian.search", PermissionLevel.READ, PolicyDecision.AUTO),
    ActionPolicy("web.search", PermissionLevel.READ, PolicyDecision.AUTO),
    ActionPolicy("clock.now", PermissionLevel.READ, PolicyDecision.AUTO),
    ActionPolicy("browser.read", PermissionLevel.READ, PolicyDecision.AUTO),
    # ---------------------------------------------------------------- level 1: open
    ActionPolicy("app.open", PermissionLevel.OPEN, PolicyDecision.AUTO),
    ActionPolicy("file.open", PermissionLevel.OPEN, PolicyDecision.AUTO),
    ActionPolicy("browser.open", PermissionLevel.OPEN, PolicyDecision.AUTO),
    ActionPolicy("browser.navigate", PermissionLevel.OPEN, PolicyDecision.AUTO),
    ActionPolicy("screen.capture", PermissionLevel.OPEN, PolicyDecision.AUTO),
    ActionPolicy("notify.show", PermissionLevel.OPEN, PolicyDecision.AUTO),
    # ---------------------------------------------------------------- level 2: modify
    ActionPolicy("file.create", PermissionLevel.MODIFY, PolicyDecision.AUTO),
    ActionPolicy("file.folder.create", PermissionLevel.MODIFY, PolicyDecision.AUTO),
    ActionPolicy("clipboard.write", PermissionLevel.MODIFY, PolicyDecision.AUTO),
    ActionPolicy("audio.volume.set", PermissionLevel.MODIFY, PolicyDecision.AUTO),
    ActionPolicy("memory.write", PermissionLevel.MODIFY, PolicyDecision.AUTO),
    ActionPolicy("obsidian.write", PermissionLevel.MODIFY, PolicyDecision.AUTO),
    ActionPolicy("browser.type", PermissionLevel.MODIFY, PolicyDecision.AUTO, RiskLevel.LOW),
    ActionPolicy("browser.click", PermissionLevel.MODIFY, PolicyDecision.AUTO, RiskLevel.LOW),
    # PART 21: modifying an existing document is automatic *with a version backup*.
    ActionPolicy(
        "file.write",
        PermissionLevel.MODIFY,
        PolicyDecision.AUTO,
        RiskLevel.MEDIUM,
        requires_backup=True,
    ),
    ActionPolicy(
        "file.copy", PermissionLevel.MODIFY, PolicyDecision.AUTO, RiskLevel.LOW, bulk_threshold=50
    ),
    ActionPolicy(
        "file.move",
        PermissionLevel.MODIFY,
        PolicyDecision.AUTO,
        RiskLevel.MEDIUM,
        bulk_threshold=10,
    ),
    ActionPolicy(
        "file.rename",
        PermissionLevel.MODIFY,
        PolicyDecision.AUTO,
        RiskLevel.MEDIUM,
        bulk_threshold=10,
    ),
    ActionPolicy("app.close", PermissionLevel.MODIFY, PolicyDecision.AUTO, RiskLevel.LOW),
    ActionPolicy(
        "system.process.stop", PermissionLevel.MODIFY, PolicyDecision.ASK_ONCE, RiskLevel.MEDIUM
    ),
    # ---------------------------------------------------------------- asked every time
    ActionPolicy(
        "file.delete", PermissionLevel.MODIFY, PolicyDecision.ASK_ALWAYS, RiskLevel.HIGH, False
    ),
    ActionPolicy(
        "powershell.run", PermissionLevel.MODIFY, PolicyDecision.ASK_ALWAYS, RiskLevel.HIGH, False
    ),
    ActionPolicy(
        "shell.run", PermissionLevel.MODIFY, PolicyDecision.ASK_ALWAYS, RiskLevel.HIGH, False
    ),
    ActionPolicy(
        "script.run", PermissionLevel.MODIFY, PolicyDecision.ASK_ALWAYS, RiskLevel.HIGH, False
    ),
    # ---------------------------------------------------------------- level 3: external
    ActionPolicy(
        "email.send", PermissionLevel.EXTERNAL, PolicyDecision.ASK_ALWAYS, RiskLevel.HIGH, False
    ),
    ActionPolicy(
        "message.send", PermissionLevel.EXTERNAL, PolicyDecision.ASK_ALWAYS, RiskLevel.HIGH, False
    ),
    ActionPolicy(
        "social.post", PermissionLevel.EXTERNAL, PolicyDecision.ASK_ALWAYS, RiskLevel.HIGH, False
    ),
    ActionPolicy(
        "purchase.make",
        PermissionLevel.EXTERNAL,
        PolicyDecision.ASK_ALWAYS,
        RiskLevel.CRITICAL,
        False,
    ),
    ActionPolicy(
        "http.post", PermissionLevel.EXTERNAL, PolicyDecision.ASK_ONCE, RiskLevel.MEDIUM, False
    ),
    ActionPolicy(
        "calendar.write", PermissionLevel.EXTERNAL, PolicyDecision.ASK_ONCE, RiskLevel.MEDIUM
    ),
    ActionPolicy(
        "browser.submit", PermissionLevel.EXTERNAL, PolicyDecision.ASK_ONCE, RiskLevel.MEDIUM, False
    ),
    ActionPolicy("cloud.inference", PermissionLevel.EXTERNAL, PolicyDecision.AUTO, RiskLevel.LOW),
    # ---------------------------------------------------------------- level 4–5: the machine
    ActionPolicy(
        "app.install", PermissionLevel.SYSTEM, PolicyDecision.ASK_ALWAYS, RiskLevel.HIGH, False
    ),
    ActionPolicy(
        "app.uninstall", PermissionLevel.SYSTEM, PolicyDecision.ASK_ALWAYS, RiskLevel.HIGH, False
    ),
    ActionPolicy(
        "service.control", PermissionLevel.SYSTEM, PolicyDecision.ASK_ALWAYS, RiskLevel.HIGH, False
    ),
    ActionPolicy(
        "system.setting.write",
        PermissionLevel.SYSTEM,
        PolicyDecision.ASK_ALWAYS,
        RiskLevel.HIGH,
        False,
    ),
    ActionPolicy(
        "registry.write", PermissionLevel.SYSTEM, PolicyDecision.ASK_ALWAYS, RiskLevel.HIGH, False
    ),
    ActionPolicy(
        "system.power", PermissionLevel.SYSTEM, PolicyDecision.ASK_ALWAYS, RiskLevel.HIGH, False
    ),
    ActionPolicy("system.lock", PermissionLevel.SYSTEM, PolicyDecision.ASK_ONCE, RiskLevel.LOW),
    ActionPolicy(
        "shell.admin", PermissionLevel.ADMIN, PolicyDecision.ASK_ALWAYS, RiskLevel.CRITICAL, False
    ),
    ActionPolicy(
        "credential.change",
        PermissionLevel.ADMIN,
        PolicyDecision.ASK_ALWAYS,
        RiskLevel.CRITICAL,
        False,
    ),
)

#: Namespace defaults, consulted when an exact action is unknown. Walking the prefixes means
#: a new verb inherits a sane level instead of always landing on the fail-closed default.
_NAMESPACE_DEFAULTS: tuple[tuple[str, PermissionLevel, PolicyDecision, RiskLevel], ...] = (
    ("audit", PermissionLevel.ADMIN, PolicyDecision.BLOCK, RiskLevel.CRITICAL),
    ("security", PermissionLevel.ADMIN, PolicyDecision.BLOCK, RiskLevel.CRITICAL),
    ("credential", PermissionLevel.ADMIN, PolicyDecision.ASK_ALWAYS, RiskLevel.CRITICAL),
    ("registry", PermissionLevel.SYSTEM, PolicyDecision.ASK_ALWAYS, RiskLevel.HIGH),
    ("service", PermissionLevel.SYSTEM, PolicyDecision.ASK_ALWAYS, RiskLevel.HIGH),
    ("powershell", PermissionLevel.MODIFY, PolicyDecision.ASK_ALWAYS, RiskLevel.HIGH),
    ("shell", PermissionLevel.MODIFY, PolicyDecision.ASK_ALWAYS, RiskLevel.HIGH),
    ("purchase", PermissionLevel.EXTERNAL, PolicyDecision.ASK_ALWAYS, RiskLevel.CRITICAL),
    ("email", PermissionLevel.EXTERNAL, PolicyDecision.ASK_ALWAYS, RiskLevel.HIGH),
    ("message", PermissionLevel.EXTERNAL, PolicyDecision.ASK_ALWAYS, RiskLevel.HIGH),
    ("social", PermissionLevel.EXTERNAL, PolicyDecision.ASK_ALWAYS, RiskLevel.HIGH),
    ("calendar", PermissionLevel.EXTERNAL, PolicyDecision.ASK_ONCE, RiskLevel.MEDIUM),
    ("http", PermissionLevel.EXTERNAL, PolicyDecision.ASK_ONCE, RiskLevel.MEDIUM),
    ("system", PermissionLevel.SYSTEM, PolicyDecision.ASK_ALWAYS, RiskLevel.HIGH),
    ("file", PermissionLevel.MODIFY, PolicyDecision.ASK_ONCE, RiskLevel.MEDIUM),
    ("browser", PermissionLevel.MODIFY, PolicyDecision.ASK_ONCE, RiskLevel.MEDIUM),
    ("app", PermissionLevel.MODIFY, PolicyDecision.ASK_ONCE, RiskLevel.MEDIUM),
    ("memory", PermissionLevel.MODIFY, PolicyDecision.ASK_ONCE, RiskLevel.LOW),
    ("obsidian", PermissionLevel.MODIFY, PolicyDecision.ASK_ONCE, RiskLevel.LOW),
    ("clipboard", PermissionLevel.MODIFY, PolicyDecision.ASK_ONCE, RiskLevel.LOW),
    ("audio", PermissionLevel.MODIFY, PolicyDecision.ASK_ONCE, RiskLevel.LOW),
    ("screen", PermissionLevel.READ, PolicyDecision.ASK_ONCE, RiskLevel.LOW),
    ("window", PermissionLevel.READ, PolicyDecision.AUTO, RiskLevel.LOW),
    ("notify", PermissionLevel.OPEN, PolicyDecision.AUTO, RiskLevel.LOW),
    ("clock", PermissionLevel.READ, PolicyDecision.AUTO, RiskLevel.NONE),
    ("web", PermissionLevel.READ, PolicyDecision.AUTO, RiskLevel.LOW),
)


class PolicyTable:
    """Lookup with user overrides layered over the defaults."""

    def __init__(self, overrides: dict[str, PolicyDecision] | None = None) -> None:
        self._policies = {p.action: p for p in _DEFAULTS}
        self._overrides = {canonical(k): v for k, v in (overrides or {}).items()}

    def get(self, action: str, *, autonomy: AutonomyLevel = AutonomyLevel.MODERATE) -> ActionPolicy:
        """Resolve a policy: exact match, then the nearest listed ancestor, then namespace.

        The ancestor step is the one that is easy to leave out and expensive to leave out
        (ADR 0007). Without it, ``file.delete.bulk`` — an action nobody listed — did not
        inherit ``file.delete``'s ASK_ALWAYS/HIGH. It fell through to the ``file`` namespace
        default of ASK_ONCE/MEDIUM, so the *more* dangerous action carried the *weaker*
        policy, and "always ask before deleting" could be sidestepped by naming the action
        something more specific.
        """
        name = canonical(action)
        policy = self._policies.get(name) or self._from_ancestor(name) or self._from_namespace(name)

        override = self._overrides.get(name)
        if override is not None and self._may_override(policy, override, name):
            policy = ActionPolicy(
                policy.action,
                policy.level,
                override,
                policy.risk,
                policy.reversible,
                policy.bulk_threshold,
                policy.requires_backup,
            )

        return self._apply_autonomy(policy, autonomy)

    def _may_override(self, policy: ActionPolicy, override: PolicyDecision, name: str) -> bool:
        """A user may loosen level 0–3, but never silently auto-approve system or admin
        work, and never downgrade an action the table says to ask about every time."""
        if self.is_blocked(name):
            return False
        if override is not PolicyDecision.AUTO:
            return True
        return not (
            policy.level >= PermissionLevel.SYSTEM or policy.default is PolicyDecision.ASK_ALWAYS
        )

    def _from_ancestor(self, name: str) -> ActionPolicy | None:
        """The nearest listed ancestor's policy, carried down to this action.

        Most specific first, so a listed rule always beats a more general one. The inherited
        policy keeps the ancestor's level, decision, risk and reversibility: a sub-action is
        a *narrower* case of its parent, and there is no reason a narrower case of "always
        ask before deleting" should ask less.
        """
        for prefix in prefixes(name)[1:]:  # [0] is the action itself, already missed
            parent = self._policies.get(prefix)
            if parent is not None:
                return ActionPolicy(
                    name,
                    parent.level,
                    parent.default,
                    parent.risk,
                    parent.reversible,
                    parent.bulk_threshold,
                    parent.requires_backup,
                )
        return None

    def _from_namespace(self, name: str) -> ActionPolicy:
        for prefix in prefixes(name):
            if prefix in HARD_BLOCKED:
                return ActionPolicy(
                    name, PermissionLevel.ADMIN, PolicyDecision.BLOCK, RiskLevel.CRITICAL, False
                )
            for namespace, level, decision, risk in _NAMESPACE_DEFAULTS:
                if prefix == namespace:
                    return ActionPolicy(name, level, decision, risk, reversible=False)
        # Nothing recognised the action at all: fail closed, at a level that forces a human.
        return ActionPolicy(
            name, PermissionLevel.MODIFY, PolicyDecision.ASK_ALWAYS, RiskLevel.MEDIUM, False
        )

    def _apply_autonomy(self, policy: ActionPolicy, autonomy: AutonomyLevel) -> ActionPolicy:
        """PART 97. Autonomy can only *tighten* the table, never loosen it."""
        if policy.default is not PolicyDecision.AUTO:
            return policy
        if autonomy is AutonomyLevel.SUGGEST_ONLY and policy.level > PermissionLevel.READ:
            return _with(policy, PolicyDecision.ASK_ONCE)
        if autonomy is AutonomyLevel.SAFE_ACTIONS and policy.level > PermissionLevel.OPEN:
            return _with(policy, PolicyDecision.ASK_ONCE)
        if autonomy is AutonomyLevel.MODERATE and policy.level >= PermissionLevel.EXTERNAL:
            return _with(policy, PolicyDecision.ASK_ONCE)
        return policy

    def override(self, action: str, decision: PolicyDecision) -> None:
        name = canonical(action)
        if self.is_blocked(name):
            raise PermissionError(f"{name!r} is hard-blocked and cannot be overridden")
        self._overrides[name] = decision

    def can_relax(self, action: str) -> bool:
        """Whether a user override to AUTO would actually take effect.

        The panel asks this before offering the choice: a control that saves a setting the
        table then ignores teaches the owner that deleting files no longer asks, when it does.
        """
        name = canonical(action)
        return self._may_override(self.get(name), PolicyDecision.AUTO, name)

    # ------------------------------------------------------------------ backup (Sprint 47)

    def export_state(self) -> list[dict]:
        """The owner's own overrides. The shipped defaults are code, not state."""
        return [
            {"action": action, "decision": decision.value}
            for action, decision in sorted(self._overrides.items())
        ]

    def import_state(self, rows: list[dict], *, replace: bool = True) -> int:
        """Reapply the owner's overrides, through `override` so the rules still hold.

        Deliberately *not* a straight assignment into `_overrides`. A backup is a file, and a
        file is external content: restoring one by writing the dict directly would let an
        edited backup auto-approve an action the table says to always ask about, which is the
        exact bypass `_may_override` exists to prevent. Anything refused is skipped and said.
        """
        if replace:
            self._overrides.clear()
        restored = 0
        for row in rows:
            try:
                self.override(row["action"], PolicyDecision(row["decision"]))
                restored += 1
            except (PermissionError, ValueError, KeyError) as exc:
                log.warning(
                    "policy_override_not_restored", action=row.get("action"), error=str(exc)
                )
        return restored

    def clear_override(self, action: str) -> None:
        """Drop a user override, returning the action to its shipped default."""
        self._overrides.pop(canonical(action), None)

    def is_blocked(self, action: str) -> bool:
        name = canonical(action)
        return any(prefix in HARD_BLOCKED for prefix in prefixes(name))

    def known_actions(self) -> list[str]:
        return sorted(self._policies)


def _with(policy: ActionPolicy, decision: PolicyDecision) -> ActionPolicy:
    return ActionPolicy(
        policy.action,
        policy.level,
        decision,
        policy.risk,
        policy.reversible,
        policy.bulk_threshold,
        policy.requires_backup,
    )
