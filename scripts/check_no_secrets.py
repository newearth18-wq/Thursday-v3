#!/usr/bin/env python3
"""Refuse to commit credential material.

Thursday refuses to write a secret into its own vault; the repository holds itself to the
same rule. Reuses the runtime redactor, so there is one definition of what a secret looks
like rather than two that drift.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packages" / "security"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packages" / "shared"))

from thursday_security.redaction import SecretRedactor

#: Files whose whole purpose is to contain example credential shapes.
ALLOWED = {
    "scripts/check_no_secrets.py",
    "packages/security/thursday_security/redaction.py",
    "tests/unit/test_security.py",
    ".env.example",
}


def main(paths: list[str]) -> int:
    redactor = SecretRedactor()
    failures: list[tuple[str, tuple[str, ...]]] = []

    for raw in paths:
        path = Path(raw)
        if str(path).replace("\\", "/") in ALLOWED or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if hits := redactor.scan(text):
            failures.append((str(path), hits))

    for path, hits in failures:
        print(f"credential material in {path}: {', '.join(hits)}", file=sys.stderr)
    if failures:
        print("\nMove it behind the SecretProvider and reference it by handle.", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
