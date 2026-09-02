"""The Permission Engine (PART 18–21).

Every action — from every agent, tool, automation, API client and device route — passes
through ``decide()``. Rules are evaluated in a fixed order and the first match wins, so a
verdict is always explainable by naming one rule.

Two invariants hold at every autonomy level, including the highest:

* an ``ASK_ALWAYS`` action is asked every time and can never become a standing grant
* a ``BLOCK`` action has no path to permitted — not by config, grant, autonomy, or an
  agent's own reasoning
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from fnmatch import fnmatch
from uuid import UUID

from thursday_shared.enums import (
    ApprovalScope,
    AutonomyLevel,
    DataSensitivity,
    PermissionLevel,
    PolicyDecision,
    RiskLevel,
    max_risk,
)
from thursday_shared.models import (
    ActionRequest,
    PermissionGrant,
    PermissionSet,
    PermissionVerdict,
)

from thursday_security.policy import PolicyTable, canonical
from thursday_security.privacy import PrivacyZoneRegistry
from thursday_security.remote import needs_confirmation_when_remote

#: Which privacy surface each action namespace touches, for zone checks (PART 51, §68).
_ACTION_SURFACE: dict[str, str] = {
    "screen.capture": "screen",
    "window.active": "screen",
    "camera.capture": "camera",
    "vision.analyze": "camera",
    "audio.listen": "microphone",
    "memory.write": "memory",
    "cloud.inference": "cloud",
}

#: Actions that carry a payload off the machine. A SECRET payload may not use any of them.
_EGRESS_ACTIONS: frozenset[str] = frozenset(
    {"cloud.inference", "http.post", "email.send", "message.send", "social.post", "web.search"}
)


class PermissionEngine:
    def __init__(
        self,
        *,
        policy: PolicyTable | None = None,
        zones: PrivacyZoneRegistry | None = None,
        autonomy: AutonomyLevel = AutonomyLevel.MODERATE,
    ) -> None:
        self.policy = policy or PolicyTable()
        self.zones = zones or PrivacyZoneRegistry()
        self.autonomy = autonomy
        self._grants: list[PermissionGrant] = []
        self._lockdown = False

    def set_autonomy(self, level: AutonomyLevel) -> None:
        """PART 97. Raising this relaxes ASK_ONCE actions only."""
        self.autonomy = level

    # ------------------------------------------------------------------ lockdown (§69)

    def set_lockdown(self, active: bool) -> None:
        self._lockdown = active

    @property
    def lockdown(self) -> bool:
        return self._lockdown

    # ------------------------------------------------------------------ grants

    def add_grant(self, grant: PermissionGrant) -> PermissionGrant:
        """Scoped and expiring by construction — 'always allow' is never global.

        An ``ASK_ALWAYS`` action is refused here rather than in the UI, so no API client can
        route around the rule by passing ``scope=always`` (ADR 0008).
        """
        grant.action = canonical(grant.action)
        if self.policy.is_blocked(grant.action):
            raise PermissionError(f"cannot grant a hard-blocked action: {grant.action}")
        if not self.policy.get(grant.action, autonomy=self.autonomy).default.grantable:
            raise PermissionError(
                f"{grant.action!r} is ASK_ALWAYS and may never become a standing grant"
            )
        if grant.expires_at is None and grant.scope is ApprovalScope.ALWAYS:
            grant.expires_at = datetime.now(UTC) + timedelta(days=30)
        self._grants.append(grant)
        return grant

    def revoke_grant(self, grant_id: UUID) -> bool:
        before = len(self._grants)
        self._grants = [g for g in self._grants if g.id != grant_id]
        return len(self._grants) < before

    def list_grants(self) -> list[PermissionGrant]:
        now = datetime.now(UTC)
        return [g for g in self._grants if not g.expires_at or g.expires_at > now]

    def _find_grant(self, req: ActionRequest) -> PermissionGrant | None:
        return next((g for g in self.list_grants() if g.matches(req)), None)

    # ------------------------------------------------------------------ the decision

    def decide(
        self,
        req: ActionRequest,
        *,
        permissions: PermissionSet | None = None,
        location: str | None = None,
        mode: str | None = None,
    ) -> PermissionVerdict:
        req.action = canonical(req.action)
        policy = self.policy.get(req.action, autonomy=self.autonomy)
        level = max(req.level, policy.level)
        risk = _max_risk(req.risk, policy.risk)
        reversible = req.reversible and policy.reversible

        # 1. Lockdown: only reading and stopping survive.
        if self._lockdown and level > PermissionLevel.READ:
            return PermissionVerdict(
                decision=PolicyDecision.BLOCK,
                reason="lockdown mode is active; only read actions are permitted",
                rule="lockdown",
                level=level,
                risk=risk,
            )

        # 2. Hard block: no override path exists, by design.
        if self.policy.is_blocked(req.action):
            return PermissionVerdict(
                decision=PolicyDecision.BLOCK,
                reason=f"{req.action!r} is permanently blocked",
                rule="hard_block",
                level=level,
                risk=RiskLevel.CRITICAL,
            )

        # 2b. A namespace-level BLOCK. `_from_namespace` can resolve an unlisted verb such
        #     as `audit.truncate` to BLOCK; without this the verdict would fall through to
        #     the fail-closed ASK, which is weaker than the policy actually says.
        if policy.default is PolicyDecision.BLOCK:
            return PermissionVerdict(
                decision=PolicyDecision.BLOCK,
                reason=f"the {req.action.split('.')[0]!r} namespace is permanently blocked",
                rule="blocked_namespace",
                level=level,
                risk=RiskLevel.CRITICAL,
            )

        # 3. Privacy zone forbids the surface this action touches.
        surface = _ACTION_SURFACE.get(req.action)
        if surface:
            zone = self.zones.forbids(
                surface,
                device_id=req.device_id,
                location=location,
                mode=mode,
                now=datetime.now(UTC).time(),
            )
            if zone:
                return PermissionVerdict(
                    decision=PolicyDecision.BLOCK,
                    reason=f"privacy zone {zone!r} disables {surface} here",
                    rule="privacy_zone",
                    level=level,
                    risk=risk,
                )

        # 3b. A SECRET payload may not leave the machine (§34, T8).
        if req.sensitivity >= DataSensitivity.SECRET and req.action in _EGRESS_ACTIONS:
            return PermissionVerdict(
                decision=PolicyDecision.BLOCK,
                reason="payload is classified SECRET and may not leave this machine",
                rule="privacy_secret",
                level=level,
                risk=RiskLevel.CRITICAL,
            )

        # 4. The agent's own envelope — intersection only, never widened here.
        if permissions is not None:
            if level > permissions.max_level:
                return PermissionVerdict(
                    decision=PolicyDecision.BLOCK,
                    reason=(
                        f"action needs level {level.name}; this context is capped at "
                        f"{permissions.max_level.name}"
                    ),
                    rule="permission_ceiling",
                    level=level,
                    risk=risk,
                )
            if permissions.allowed_tools and req.action not in permissions.allowed_tools:
                return PermissionVerdict(
                    decision=PolicyDecision.BLOCK,
                    reason=f"{req.action!r} is not in this context's allowed tools",
                    rule="tool_allowlist",
                    level=level,
                    risk=risk,
                )
            if (
                permissions.path_scopes
                and req.resource
                and not any(fnmatch(req.resource, s) for s in permissions.path_scopes)
            ):
                return PermissionVerdict(
                    decision=PolicyDecision.BLOCK,
                    reason=f"{req.resource!r} is outside this context's path scopes",
                    rule="path_scope",
                    level=level,
                    risk=risk,
                )

        # 4b. The instruction came from one machine and will run on another (V8).
        #
        #     Ahead of the grant check on purpose. A grant is a decision the owner made in
        #     one situation, and "I approved deleting files while sitting at my PC" is not
        #     the same decision as "anything holding my phone may delete files on my PC".
        #     Letting a grant satisfy a remote consequential action would turn one local
        #     approval into a standing remote capability, which is the opposite of what the
        #     owner agreed to.
        #
        #     ASK_ALWAYS rather than ASK_ONCE for the same reason: `ASK_ONCE` may be
        #     remembered as a grant, and a standing grant is exactly what must not exist
        #     here (ADR 0008).
        if req.is_remote and needs_confirmation_when_remote(req.action, level=level):
            return PermissionVerdict(
                decision=PolicyDecision.ASK_ALWAYS,
                reason=(
                    f"{req.action!r} is being run on another machine from somewhere you "
                    "cannot see the result"
                ),
                rule="remote_command",
                level=level,
                risk=risk,
            )

        # 5. A standing, scoped, unexpired grant.
        if (grant := self._find_grant(req)) is not None:
            if grant.uses_remaining is not None:
                grant.uses_remaining -= 1
            return PermissionVerdict(
                decision=PolicyDecision.AUTO,
                reason=f"covered by an existing grant for {grant.action!r} on {grant.resource_glob}",
                rule="standing_grant",
                level=level,
                risk=risk,
                grant_id=grant.id,
            )

        # 6. Explicit ASK from policy. ASK_ALWAYS is reported as such so the approval UI
        #    knows not to offer "always allow".
        if policy.default.requires_approval:
            return PermissionVerdict(
                decision=policy.default,
                reason=(
                    f"policy for {req.action!r} requires approval every time"
                    if policy.default is PolicyDecision.ASK_ALWAYS
                    else f"policy for {req.action!r} requires approval"
                ),
                rule="policy_default_ask",
                level=level,
                risk=risk,
            )

        # 7. Level, risk, reversibility and blast radius can each upgrade AUTO to ASK.
        if level >= PermissionLevel.EXTERNAL:
            return PermissionVerdict(
                decision=PolicyDecision.ASK_ONCE,
                reason=f"level {level.name} actions affect the world outside this machine",
                rule="external_level",
                level=level,
                risk=risk,
            )
        if risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            return PermissionVerdict(
                decision=PolicyDecision.ASK_ALWAYS,
                reason=f"risk is {risk.value}",
                rule="high_risk",
                level=level,
                risk=risk,
            )
        if not reversible:
            return PermissionVerdict(
                decision=PolicyDecision.ASK_ALWAYS,
                reason="the action has no undo path",
                rule="irreversible",
                level=level,
                risk=risk,
            )
        if policy.bulk_threshold is not None and req.object_count > policy.bulk_threshold:
            return PermissionVerdict(
                decision=PolicyDecision.ASK_ONCE,
                reason=(
                    f"{req.object_count} objects exceeds the {policy.bulk_threshold}-object "
                    "threshold for unattended changes"
                ),
                rule="blast_radius",
                level=level,
                risk=risk,
            )

        # 8. Ordinary, reversible, in-scope work.
        if policy.default is PolicyDecision.AUTO and level <= PermissionLevel.MODIFY:
            return PermissionVerdict(
                decision=PolicyDecision.AUTO,
                reason=(
                    f"{req.action!r} is a reversible level-{int(level)} action in scope"
                    + (" (a version backup is taken first)" if policy.requires_backup else "")
                ),
                rule="policy_default_auto",
                level=level,
                risk=risk,
                requires_backup=policy.requires_backup,
            )

        return PermissionVerdict(
            decision=PolicyDecision.ASK_ALWAYS,
            reason="no rule authorises this automatically",
            rule="fail_closed",
            level=level,
            risk=risk,
        )


def _max_risk(a: RiskLevel, b: RiskLevel) -> RiskLevel:
    """Delegates to the shared helper — `RiskLevel` is a StrEnum, so a plain `max()` would
    order these alphabetically and rank LOW above HIGH."""
    return max_risk(a, b)
