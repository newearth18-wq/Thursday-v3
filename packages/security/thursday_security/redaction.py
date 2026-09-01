"""Secret redaction (§35, threat T2).

Runs on every prompt, vault-adjacent write, Obsidian note, memory write and log line.
The rule is blunt on purpose: a false positive costs a redacted string, a false negative
costs a leaked credential.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

REDACTED = "«redacted»"

#: Ordered most-specific first so a token is not partially matched by a generic rule.
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}")),
    ("openai_key", re.compile(r"sk-(?:proj-)?[A-Za-z0-9_\-]{20,}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}")),
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("google_key", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    (
        "private_key",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----"),
    ),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}")),
    (
        "assignment",
        re.compile(
            r"(?i)\b(api[_\-]?key|secret|password|passwd|pwd|token|client[_\-]?secret)\b"
            r"\s*[:=]\s*[\"']?([^\s\"',;]{6,})[\"']?"
        ),
    ),
    ("connection_string", re.compile(r"(?i)\b[a-z+]{2,12}://[^\s:@/]+:[^\s:@/]+@[^\s]+")),
]


@dataclass(frozen=True)
class RedactionResult:
    text: str
    hits: tuple[str, ...]

    @property
    def clean(self) -> bool:
        return not self.hits


class SecretRedactor:
    """Stateless. Cheap enough to run on every egress path."""

    def scan(self, text: str) -> tuple[str, ...]:
        return tuple(name for name, pattern in PATTERNS if pattern.search(text))

    def redact(self, text: str) -> RedactionResult:
        hits: list[str] = []
        out = text
        for name, pattern in PATTERNS:
            if not pattern.search(out):
                continue
            hits.append(name)
            if name == "assignment":
                # Keep the key name, drop only the value — the shape stays debuggable.
                out = pattern.sub(lambda m: f"{m.group(1)}={REDACTED}", out)
            else:
                out = pattern.sub(REDACTED, out)
        return RedactionResult(text=out, hits=tuple(hits))

    def assert_clean(self, text: str, *, where: str) -> None:
        """Refuse the write outright. Used where redaction is not good enough (§8 vault)."""
        from thursday_shared.errors import SecretLeakBlocked

        hits = self.scan(text)
        if hits:
            raise SecretLeakBlocked(
                f"credential material blocked before reaching {where}",
                patterns=list(hits),
                where=where,
            )


def redact_dict(data: dict, redactor: SecretRedactor | None = None) -> dict:
    """Redact recursively for the ``*_summary`` projections persisted in audit rows (§4.2)."""
    r = redactor or SecretRedactor()

    def walk(value: object) -> object:
        if isinstance(value, str):
            return r.redact(value).text
        if isinstance(value, dict):
            return {k: (REDACTED if _sensitive_key(k) else walk(v)) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v) for v in value]
        return value

    return walk(data)  # type: ignore[return-value]


_SENSITIVE_KEYS = {
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "client_secret",
    "private_key",
    "authorization",
}


def _sensitive_key(key: str) -> bool:
    return key.lower().replace("-", "_") in _SENSITIVE_KEYS
