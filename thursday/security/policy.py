"""Action policy (§37).

Policies are data so a user can override them — except the BLOCK set, which is code and has
no path to `AUTO` through conversation, configuration, or an agent's own reasoning (T1).
"""

from __future__ import annotations

from dataclasses import dataclass

from thursday.shared.enums import PermissionLevel, PolicyDecision, RiskLevel


@dataclass(frozen=True)
class ActionPolicy:
    action: str
    level: PermissionLevel
    default: PolicyDecision
    risk: RiskLevel = RiskLevel.LOW
    reversible: bool = True
    #: Above this many affected objects, an AUTO action becomes ASK (blast-radius cap).
    bulk_threshold: int | None = None


#: Actions that are never permitted, whatever the user, agent, or grant says (§37, §70).
HARD_BLOCKED: frozenset[str] = frozenset(
    {
        "disable_antivirus",
        "disable_firewall",
        "disable_security_tooling",
        "disable_audit_log",
        "modify_audit_log",
        "delete_audit_log",
        "exfiltrate_secret",
        "read_vault_raw",
        "modify_permission_policy",
        "grant_self_admin",
        "disable_approval_engine",
        "format_disk",
        "delete_system_directory",
    }
)

_DEFAULTS: tuple[ActionPolicy, ...] = (
    # level 0–1: observing and opening
    ActionPolicy("read_file", PermissionLevel.READ, PolicyDecision.AUTO),
    ActionPolicy("list_dir", PermissionLevel.READ, PolicyDecision.AUTO),
    ActionPolicy("search_files", PermissionLevel.READ, PolicyDecision.AUTO),
    ActionPolicy("system_info", PermissionLevel.READ, PolicyDecision.AUTO),
    ActionPolicy("process_status", PermissionLevel.READ, PolicyDecision.AUTO),
    ActionPolicy("read_active_window", PermissionLevel.READ, PolicyDecision.AUTO),
    ActionPolicy("screenshot", PermissionLevel.READ, PolicyDecision.AUTO, RiskLevel.LOW),
    ActionPolicy("clipboard_get", PermissionLevel.READ, PolicyDecision.AUTO),
    ActionPolicy("memory_search", PermissionLevel.READ, PolicyDecision.AUTO),
    ActionPolicy("web_search", PermissionLevel.READ, PolicyDecision.AUTO),
    ActionPolicy("get_volume", PermissionLevel.READ, PolicyDecision.AUTO),
    ActionPolicy("open_app", PermissionLevel.OPEN, PolicyDecision.AUTO),
    ActionPolicy("open_file", PermissionLevel.OPEN, PolicyDecision.AUTO),
    ActionPolicy("open_url", PermissionLevel.OPEN, PolicyDecision.AUTO),
    ActionPolicy("notify", PermissionLevel.OPEN, PolicyDecision.AUTO),
    # level 2: modifying the user's own workspace
    ActionPolicy("create_folder", PermissionLevel.MODIFY, PolicyDecision.AUTO),
    ActionPolicy("write_file", PermissionLevel.MODIFY, PolicyDecision.AUTO, RiskLevel.LOW),
    ActionPolicy("save_file", PermissionLevel.MODIFY, PolicyDecision.AUTO),
    ActionPolicy("clipboard_set", PermissionLevel.MODIFY, PolicyDecision.AUTO),
    ActionPolicy("set_volume", PermissionLevel.MODIFY, PolicyDecision.AUTO),
    ActionPolicy("close_app", PermissionLevel.MODIFY, PolicyDecision.AUTO, RiskLevel.LOW),
    ActionPolicy("obsidian_write", PermissionLevel.MODIFY, PolicyDecision.AUTO),
    ActionPolicy("memory_write", PermissionLevel.MODIFY, PolicyDecision.AUTO),
    ActionPolicy(
        "rename", PermissionLevel.MODIFY, PolicyDecision.AUTO, RiskLevel.MEDIUM, bulk_threshold=10
    ),
    ActionPolicy(
        "move", PermissionLevel.MODIFY, PolicyDecision.AUTO, RiskLevel.MEDIUM, bulk_threshold=10
    ),
    ActionPolicy(
        "copy", PermissionLevel.MODIFY, PolicyDecision.AUTO, RiskLevel.LOW, bulk_threshold=50
    ),
    # level 2–3: risky or outward-facing
    ActionPolicy("delete", PermissionLevel.MODIFY, PolicyDecision.ASK, RiskLevel.HIGH, False),
    ActionPolicy("run_shell", PermissionLevel.MODIFY, PolicyDecision.ASK, RiskLevel.HIGH, False),
    ActionPolicy("run_script", PermissionLevel.MODIFY, PolicyDecision.ASK, RiskLevel.HIGH, False),
    ActionPolicy("send_email", PermissionLevel.EXTERNAL, PolicyDecision.ASK, RiskLevel.HIGH, False),
    ActionPolicy("send_message", PermissionLevel.EXTERNAL, PolicyDecision.ASK, RiskLevel.HIGH, False),
    ActionPolicy("http_post", PermissionLevel.EXTERNAL, PolicyDecision.ASK, RiskLevel.MEDIUM, False),
    ActionPolicy("calendar_write", PermissionLevel.EXTERNAL, PolicyDecision.ASK, RiskLevel.MEDIUM),
    ActionPolicy("purchase", PermissionLevel.EXTERNAL, PolicyDecision.ASK, RiskLevel.CRITICAL, False),
    ActionPolicy("publish", PermissionLevel.EXTERNAL, PolicyDecision.ASK, RiskLevel.HIGH, False),
    # level 4–5: the machine itself
    ActionPolicy("install_software", PermissionLevel.SYSTEM, PolicyDecision.ASK, RiskLevel.HIGH, False),
    ActionPolicy("uninstall_software", PermissionLevel.SYSTEM, PolicyDecision.ASK, RiskLevel.HIGH, False),
    ActionPolicy("service_control", PermissionLevel.SYSTEM, PolicyDecision.ASK, RiskLevel.HIGH, False),
    ActionPolicy("registry_write", PermissionLevel.SYSTEM, PolicyDecision.ASK, RiskLevel.HIGH, False),
    ActionPolicy("power", PermissionLevel.SYSTEM, PolicyDecision.ASK, RiskLevel.HIGH, False),
    ActionPolicy("lock", PermissionLevel.SYSTEM, PolicyDecision.ASK, RiskLevel.LOW),
    ActionPolicy("elevate", PermissionLevel.ADMIN, PolicyDecision.ASK, RiskLevel.CRITICAL, False),
    ActionPolicy("credential_change", PermissionLevel.ADMIN, PolicyDecision.ASK, RiskLevel.CRITICAL, False),
)


class PolicyTable:
    """Lookup with user overrides layered over the defaults."""

    def __init__(self, overrides: dict[str, PolicyDecision] | None = None) -> None:
        self._policies = {p.action: p for p in _DEFAULTS}
        self._overrides = dict(overrides or {})

    def get(self, action: str) -> ActionPolicy:
        """Unknown actions are ASK, not AUTO — fail closed."""
        policy = self._policies.get(action)
        if policy is None:
            return ActionPolicy(action, PermissionLevel.MODIFY, PolicyDecision.ASK, RiskLevel.MEDIUM)
        override = self._overrides.get(action)
        if override is None or action in HARD_BLOCKED:
            return policy
        if override is PolicyDecision.AUTO and policy.level >= PermissionLevel.SYSTEM:
            # A user may loosen level 0–3, but never silently auto-approve system/admin work.
            return policy
        return ActionPolicy(
            policy.action, policy.level, override, policy.risk, policy.reversible,
            policy.bulk_threshold,
        )

    def override(self, action: str, decision: PolicyDecision) -> None:
        if action in HARD_BLOCKED:
            raise PermissionError(f"{action!r} is hard-blocked and cannot be overridden")
        self._overrides[action] = decision

    def is_blocked(self, action: str) -> bool:
        return action in HARD_BLOCKED

    def known_actions(self) -> list[str]:
        return sorted(self._policies)
