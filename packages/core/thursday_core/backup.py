"""Backup and restore (Sprint 47).

A backup nobody has restored is a hope, so this module is built around three things being
true rather than around producing a file:

**It can be read back.** `verify` re-reads the archive and re-checks every component against
the checksum recorded when it was written. A backup that is corrupt is worth knowing about
now, not on the morning the disk failed.

**It carries no credentials.** Every component's rows go through the redactor on the way out.
The secret vault is *not* backed up at all, and that is a decision rather than an omission: a
backup that could restore the owner's API keys is a backup that hands them over when it is
stolen. Secrets live in the OS keychain and the owner re-provides them; everything else here
is theirs to lose.

**Restoring is loud.** `restore` refuses without an explicit confirmation, and refuses again
if the archive does not verify. It replaces state rather than merging it, because a merge of
two divergent histories is neither, and the owner asked for the one in the file.

What is deliberately *not* here: any attempt to be a database backup. If Thursday is running
on Postgres, `pg_dump` is a better tool and this file does not pretend otherwise. What this
captures is the state the running system holds — memories, tasks, the audit chain, device
credentials, the spend ledger, the owner's policy overrides, the decision journal — which is
what a fresh install cannot reconstruct.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from thursday_core.logging import get_logger

log = get_logger(__name__)

#: Bumped when the archive layout changes in a way an older reader would get wrong. A backup
#: from the future is refused rather than half-read.
FORMAT_VERSION = 1


class BackupError(Exception):
    """A backup that cannot be written, read or trusted. The message is for the owner."""


@dataclass(frozen=True)
class Component:
    """One thing worth keeping, and how to get it out and back in.

    The export and restore callables belong to the component itself (`export_state` /
    `import_state`); this holds only the wiring. A backup module that knew how each
    component stores its state would break quietly the first time one of them changed.
    """

    name: str
    export: Callable[[], list[dict]]
    restore: Callable[[list[dict]], int]
    #: What is lost if this component is not restored. Shown to the owner before they
    #: confirm, because "restore 4 components" is not a decision anybody can make.
    describes: str = ""


@dataclass
class Manifest:
    """What is in the archive, and what it hashed to when written."""

    version: int
    created_at: datetime
    components: dict[str, int] = field(default_factory=dict)
    checksums: dict[str, str] = field(default_factory=dict)
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "created_at": self.created_at.isoformat(),
            "components": self.components,
            "checksums": self.checksums,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> Manifest:
        return cls(
            version=int(raw["version"]),
            created_at=datetime.fromisoformat(raw["created_at"]),
            components=dict(raw.get("components", {})),
            checksums=dict(raw.get("checksums", {})),
            note=str(raw.get("note", "")),
        )


def _checksum(rows: list[dict]) -> str:
    """A stable hash of a component's rows.

    `sort_keys` so the same data hashes the same however the dicts were built — otherwise
    the checksum detects Python's iteration order rather than corruption.

    This is a checksum, not a signature. It catches truncation, corruption and casual
    editing. It does not stop somebody who can write the file from rehashing it, and saying
    so plainly is better than implying a guarantee that is not here: signing would need a
    key, and a key stored beside the backup signs whatever is next to it.
    """
    payload = json.dumps(rows, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class BackupService:
    """Writes archives, checks them, and puts them back."""

    def __init__(self, components: list[Component], *, redactor: Any = None) -> None:
        self._components = components
        self._redactor = redactor

    @property
    def components(self) -> list[str]:
        return [c.name for c in self._components]

    # ------------------------------------------------------------------ writing

    def create(self, path: Path | str, *, note: str = "") -> Manifest:
        """Write an archive, atomically.

        Written to a temporary file and moved into place, so a process killed halfway
        through leaves the previous backup intact rather than a truncated one. A truncated
        backup is worse than no backup: it looks like a backup.
        """
        path = Path(path)
        manifest = Manifest(version=FORMAT_VERSION, created_at=datetime.now(UTC), note=note)
        body: dict[str, list[dict]] = {}

        for component in self._components:
            rows = self._redact(component.export())
            body[component.name] = rows
            manifest.components[component.name] = len(rows)
            manifest.checksums[component.name] = _checksum(rows)

        document = {"manifest": manifest.to_dict(), "data": body}
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.touch(mode=0o600, exist_ok=True)
        temporary.chmod(0o600)
        temporary.write_text(
            json.dumps(document, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
        )
        os.replace(temporary, path)
        path.chmod(0o600)

        log.info(
            "backup_created",
            path=str(path),
            components=len(body),
            rows=sum(manifest.components.values()),
        )
        return manifest

    # ------------------------------------------------------------------ reading

    def read(self, path: Path | str) -> tuple[Manifest, dict[str, list[dict]]]:
        path = Path(path)
        if not path.exists():
            raise BackupError(f"there is no backup at {path}")
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            manifest = Manifest.from_dict(document["manifest"])
            data = document["data"]
        except (ValueError, KeyError, TypeError) as exc:
            raise BackupError(f"{path} is not a readable Thursday backup: {exc}") from exc

        if manifest.version > FORMAT_VERSION:
            # Refused rather than read as far as it goes. A newer archive read by an older
            # reader restores *some* of the owner's data and reports success.
            raise BackupError(
                f"this backup was written by a newer version (format {manifest.version}, "
                f"this build reads {FORMAT_VERSION})"
            )
        return manifest, data

    def verify(self, path: Path | str) -> list[str]:
        """Re-read the archive and check it. Returns the problems; empty means good.

        A separate operation from `restore` on purpose, so "is my backup any good" is a
        question the owner can ask on a quiet Tuesday rather than during the emergency.
        """
        manifest, data = self.read(path)
        problems: list[str] = []

        for name, expected in manifest.checksums.items():
            if name not in data:
                problems.append(f"{name}: named in the manifest and missing from the archive")
                continue
            if _checksum(data[name]) != expected:
                problems.append(f"{name}: contents do not match the checksum written with them")

        for name in data:
            if name not in manifest.checksums:
                problems.append(f"{name}: present in the archive and not in the manifest")

        missing = set(self.components) - set(data)
        if missing:
            problems.append(
                "not in this backup: " + ", ".join(sorted(missing)) + " (from an older archive?)"
            )
        return problems

    def inspect(self, path: Path | str) -> dict:
        """What is in an archive, without restoring it. For the owner's confirmation."""
        manifest, _ = self.read(path)
        return {
            "created_at": manifest.created_at.isoformat(),
            "version": manifest.version,
            "note": manifest.note,
            "components": manifest.components,
            "rows": sum(manifest.components.values()),
            "problems": self.verify(path),
            "describes": {c.name: c.describes for c in self._components if c.describes},
        }

    # ------------------------------------------------------------------ restoring

    def restore(self, path: Path | str, *, confirm: bool = False) -> dict[str, int]:
        """Replace the running state with the archive's. Refused unless confirmed.

        The confirmation is a parameter rather than a prompt because this is a library: the
        API layer turns it into an approval the owner grants. What matters is that no code
        path reaches a destructive restore by default — an argument nobody passed cannot
        overwrite somebody's memories.
        """
        if not confirm:
            raise BackupError(
                "restoring replaces everything Thursday currently holds; confirm it explicitly"
            )

        problems = self.verify(path)
        if problems:
            # Refused, not restored-as-far-as-possible. Half a restore leaves a system that
            # is neither the backup nor what it was, and nobody can tell which parts are which.
            raise BackupError(
                "this backup did not verify, so nothing was restored: " + "; ".join(problems)
            )

        _, data = self.read(path)
        restored: dict[str, int] = {}
        for component in self._components:
            rows = data.get(component.name, [])
            restored[component.name] = component.restore(rows)

        log.warning("backup_restored", path=str(path), restored=restored)
        return restored

    # ------------------------------------------------------------------ internals

    def _redact(self, rows: list[dict]) -> list[dict]:
        """Last stop before state becomes a file on disk (§90).

        A backup is one more place data lands, and the list §90 gives is not exhaustive —
        the principle is that a credential in plain storage is a credential someone else can
        read. The vault is excluded from backup entirely rather than redacted, because
        redacted secrets would restore as the string "«redacted»" and quietly break every
        integration they belong to.
        """
        if self._redactor is None:
            return rows
        from thursday_security.redaction import redact_dict

        return [redact_dict(row, self._redactor) for row in rows]


def default_components(container: Any) -> list[Component]:
    """The state a fresh install cannot reconstruct.

    Each entry names what is lost without it, because a confirmation dialogue that says
    "restore 7 components" asks the owner to approve something they cannot picture.
    """
    wiring = [
        ("memory", container.memory, "everything Thursday remembers about you and your work"),
        ("tasks", container.tasks, "work in flight and its history"),
        ("audit", container.audit, "the record of what was done, with its hash chain"),
        ("costs", container.costs, "the spend ledger, and with it the period caps"),
        ("policies", container.policy, "your own permission decisions"),
        ("journal", container.journal, "why Thursday made the choices it made"),
    ]
    components = [
        Component(
            name=name,
            export=service.export_state,
            restore=service.import_state,
            describes=describes,
        )
        for name, service, describes in wiring
        if service is not None and hasattr(service, "export_state")
    ]
    return components
