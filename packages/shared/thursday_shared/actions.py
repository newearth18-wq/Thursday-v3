"""Action naming (PART 23, ADR 0007).

Actions are namespaced — ``file.read``, ``system.process.stop`` — and the namespace does
real work: the policy table, the capability registry and the undo registry all resolve an
unknown verb by walking up its prefixes, so a new command inherits sane defaults instead of
falling through to "unknown".

This module is the single source of truth for the vocabulary. It lives in ``shared`` because
security, core and devices all need it, and none of them may import the others.
"""

from __future__ import annotations

#: V1's flat names, kept resolving for one release so an older node or a stored automation
#: does not break on upgrade.
LEGACY_ALIASES: dict[str, str] = {
    "open_app": "app.open",
    "close_app": "app.close",
    "open_file": "file.open",
    "read_file": "file.read",
    "write_file": "file.write",
    "save_file": "file.write",
    "create_folder": "file.folder.create",
    "list_dir": "file.list",
    "search_files": "file.search",
    "delete": "file.delete",
    "move": "file.move",
    "rename": "file.rename",
    "copy": "file.copy",
    "run_shell": "shell.run",
    "run_script": "script.run",
    "process_status": "system.process.list",
    "system_info": "system.info",
    "read_active_window": "window.active",
    "screenshot": "screen.capture",
    "clipboard_get": "clipboard.read",
    "clipboard_set": "clipboard.write",
    "get_volume": "audio.volume.get",
    "set_volume": "audio.volume.set",
    "notify": "notify.show",
    "lock": "system.lock",
    "power": "system.power",
    "memory_search": "memory.search",
    "memory_write": "memory.write",
    "memory_forget": "memory.forget",
    "obsidian_write": "obsidian.write",
    "obsidian_search": "obsidian.search",
    "obsidian_restore": "obsidian.restore",
    "web_search": "web.search",
    "clock": "clock.now",
    "send_email": "email.send",
    "send_message": "message.send",
    "publish": "social.post",
    "purchase": "purchase.make",
    "http_post": "http.post",
    "calendar_write": "calendar.write",
    "install_software": "app.install",
    "uninstall_software": "app.uninstall",
    "elevate": "shell.admin",
    "credential_change": "credential.change",
    "cloud_inference": "cloud.inference",
    "restore_file": "file.restore",
    "delete_folder": "file.folder.delete",
    "restore_from_trash": "file.restore_from_trash",
    "disable_antivirus": "security.antivirus.disable",
    "disable_firewall": "security.firewall.disable",
    "disable_security_tooling": "security.disable",
    "disable_audit_log": "audit.disable",
    "modify_audit_log": "audit.modify",
    "delete_audit_log": "audit.delete",
    "exfiltrate_secret": "credential.export",
    "read_vault_raw": "vault.read_raw",
    "modify_permission_policy": "permission.policy.modify",
    "grant_self_admin": "permission.self_grant",
    "disable_approval_engine": "approval.disable",
    "format_disk": "disk.format",
    "delete_system_directory": "system.directory.delete",
}


def canonical(action: str) -> str:
    """Resolve a legacy flat name to its namespaced form. Idempotent."""
    return LEGACY_ALIASES.get(action, action)


def prefixes(action: str) -> list[str]:
    """``file.folder.create`` → ``['file.folder.create', 'file.folder', 'file']``.

    Most specific first, so the first match wins and a specific rule always beats its
    namespace's default.
    """
    parts = action.split(".")
    return [".".join(parts[: i + 1]) for i in range(len(parts))][::-1]


def namespace(action: str) -> str:
    """The leading segment: ``file.folder.create`` → ``file``."""
    return canonical(action).split(".")[0]
