"""Obsidian vault integration (§8).

The vault is the *human-readable* second brain. Postgres is the machine's. They are synced,
not merged: every note carries frontmatter linking it back to its record, so a note a person
edits by hand can be re-ingested.

Nothing that looks like a credential is ever written here — the writer refuses rather than
redacting, because a half-written secret in a plaintext vault is still a leaked secret.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from thursday_core.logging import get_logger
from thursday_security.redaction import SecretRedactor

log = get_logger(__name__)

FOLDERS: tuple[str, ...] = (
    "00 Inbox",
    "01 Projects",
    "02 Areas",
    "03 Knowledge",
    "04 People",
    "05 Meetings",
    "06 Decisions",
    "07 Skills",
    "08 Daily",
    "09 Archive",
)

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename(title: str, *, max_length: int = 120) -> str:
    cleaned = _UNSAFE.sub("-", title).strip(" .")
    return (cleaned or "untitled")[:max_length]


class ObsidianVault:
    def __init__(
        self, root: Path, *, redactor: SecretRedactor | None = None, enabled: bool = True
    ) -> None:
        self.root = Path(root)
        self.enabled = enabled
        self._redactor = redactor or SecretRedactor()

    def ensure_structure(self) -> None:
        if not self.enabled:
            return
        for folder in FOLDERS:
            (self.root / folder).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ writing

    def write_note(
        self,
        *,
        folder: str,
        title: str,
        body: str,
        frontmatter: dict[str, Any] | None = None,
        overwrite: bool = True,
    ) -> Path | None:
        """Write one note. Returns the path, or None when the vault is disabled."""
        if not self.enabled:
            return None
        # Refuse, do not redact: §8 says secrets must not reach the vault at all.
        self._redactor.assert_clean(body, where="the Obsidian vault")
        self._redactor.assert_clean(title, where="the Obsidian vault")

        directory = self.root / folder
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{safe_filename(title)}.md"
        if path.exists() and not overwrite:
            path = directory / f"{safe_filename(title)} {datetime.now(UTC):%H%M%S}.md"

        meta = {
            "thursday": True,
            "updated": datetime.now(UTC).isoformat(timespec="seconds"),
            **(frontmatter or {}),
        }
        path.write_text(_render(meta, body), encoding="utf-8")
        log.info("obsidian_note_written", path=str(path))
        return path

    def append_section(self, path: Path, heading: str, content: str) -> Path:
        self._redactor.assert_clean(content, where="the Obsidian vault")
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{existing.rstrip()}\n\n## {heading}\n\n{content}\n", encoding="utf-8")
        return path

    # ------------------------------------------------------------------ note kinds

    def daily_note(self, entry: str, *, when: datetime | None = None) -> Path | None:
        when = when or datetime.now(UTC)
        title = f"{when:%Y-%m-%d}"
        path = self.root / "08 Daily" / f"{title}.md"
        if path.exists():
            return self.append_section(path, f"{when:%H:%M}", entry)
        return self.write_note(
            folder="08 Daily",
            title=title,
            body=f"## {when:%H:%M}\n\n{entry}\n",
            frontmatter={"type": "daily", "date": title},
        )

    def decision_log(
        self,
        *,
        decision: str,
        reason: str,
        alternatives: list[str],
        source: str,
        impact: str,
        project: str | None = None,
    ) -> Path | None:
        """§55 — the decision journal entry Thursday writes when something is decided."""
        when = datetime.now(UTC)
        body = "\n".join(
            [
                f"**Decision** — {decision}",
                "",
                f"**Date** — {when:%Y-%m-%d %H:%M} UTC",
                "",
                f"**Reason** — {reason}",
                "",
                "**Alternatives considered**",
                *([f"- {a}" for a in alternatives] or ["- (none recorded)"]),
                "",
                f"**Source** — {source}",
                "",
                f"**Impact** — {impact}",
            ]
        )
        return self.write_note(
            folder="06 Decisions",
            title=f"{when:%Y-%m-%d} {decision}",
            body=body,
            frontmatter={"type": "decision", "project": project, "date": f"{when:%Y-%m-%d}"},
            overwrite=False,
        )

    def project_page(
        self, *, name: str, goal: str, status: str, sections: dict[str, str]
    ) -> Path | None:
        body = f"**Goal** — {goal}\n\n**Status** — {status}\n\n" + "\n\n".join(
            f"## {heading}\n\n{content}" for heading, content in sections.items()
        )
        return self.write_note(
            folder="01 Projects",
            title=name,
            body=body,
            frontmatter={"type": "project", "status": status},
        )

    def memory_note(
        self, *, memory_id: UUID, layer: str, content: str, source: str, confidence: float
    ) -> Path | None:
        return self.write_note(
            folder="03 Knowledge",
            title=content[:60],
            body=content,
            frontmatter={
                "type": "memory",
                "thursday_id": str(memory_id),
                "layer": layer,
                "source": source,
                "confidence": round(confidence, 2),
            },
            overwrite=False,
        )

    def update_note(
        self, relative: str, *, body: str | None = None, frontmatter: dict[str, Any] | None = None
    ) -> Path | None:
        """Rewrite an existing note, keeping the frontmatter it already had.

        Merging rather than replacing the metadata is the point: a note carries `type`,
        `thursday_id` and whatever the owner added by hand, and an update that dropped those
        would quietly sever the link between the note and the thing it describes.
        """
        if not self.enabled:
            return None
        path = self.root / relative
        if not path.exists():
            return None

        existing_meta, existing_body = _parse(path.read_text(encoding="utf-8"))
        new_body = existing_body if body is None else body
        self._redactor.assert_clean(new_body, where="the Obsidian vault")

        meta = {
            **existing_meta,
            **(frontmatter or {}),
            "updated": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        path.write_text(_render(meta, new_body), encoding="utf-8")
        log.info("obsidian_note_updated", path=str(path))
        return path

    def link_notes(self, source: str, target: str, *, relation: str = "related") -> Path | None:
        """Add a wiki-link from one note to another, under a "Links" section.

        One direction only, and idempotent. Writing both ends would double every edge on the
        second call and leave the vault fighting whatever the owner did by hand; Obsidian's
        own backlinks pane already shows the reverse.
        """
        if not self.enabled:
            return None
        path = self.root / source
        target_path = self.root / target
        if not path.exists() or not target_path.exists():
            return None

        meta, body = _parse(path.read_text(encoding="utf-8"))
        link = f"- {relation}: [[{target_path.stem}]]"
        if link in body:
            return path

        if "## Links" in body:
            body = body.replace("## Links", f"## Links\n{link}", 1)
        else:
            body = f"{body.rstrip()}\n\n## Links\n{link}\n"
        path.write_text(_render(meta, body), encoding="utf-8")
        log.debug("obsidian_notes_linked", source=source, target=target, relation=relation)
        return path

    def tag_note(self, relative: str, *tags: str) -> Path | None:
        """Add tags, as a set. Obsidian treats a repeated tag as one, and so does this."""
        if not self.enabled:
            return None
        path = self.root / relative
        if not path.exists():
            return None

        meta, body = _parse(path.read_text(encoding="utf-8"))
        existing = _as_list(meta.get("tags"))
        merged = sorted({*existing, *(t.strip().lstrip("#") for t in tags if t.strip())})
        meta["tags"] = merged
        path.write_text(_render(meta, body), encoding="utf-8")
        return path

    def meeting_note(
        self, *, title: str, attendees: list[str], notes: str, when: datetime | None = None
    ) -> Path | None:
        when = when or datetime.now(UTC)
        body = "\n".join(
            [
                f"**When** — {when:%Y-%m-%d %H:%M} UTC",
                "",
                "**Attendees**",
                *([f"- {a}" for a in attendees] or ["- (not recorded)"]),
                "",
                "## Notes",
                "",
                notes,
            ]
        )
        return self.write_note(
            folder="05 Meetings",
            title=f"{when:%Y-%m-%d} {title}",
            body=body,
            frontmatter={"type": "meeting", "date": f"{when:%Y-%m-%d}", "attendees": attendees},
            overwrite=False,
        )

    def person_note(self, *, name: str, notes: str, role: str = "") -> Path | None:
        return self.write_note(
            folder="04 People",
            title=name,
            body=(f"**Role** — {role}\n\n" if role else "") + notes,
            frontmatter={"type": "person", "role": role or None},
        )

    def skill_note(self, *, name: str, description: str, steps: list[str]) -> Path | None:
        body = f"{description}\n\n## Steps\n\n" + "\n".join(
            f"{i}. {step}" for i, step in enumerate(steps, start=1)
        )
        return self.write_note(
            folder="07 Skills",
            title=name,
            body=body,
            frontmatter={"type": "skill", "steps": len(steps)},
        )

    def archive(self, relative: str) -> Path | None:
        """Move a note to 09 Archive rather than deleting it.

        Archiving is not forgetting. Memory deletion is handled by the memory manager, where
        "forget this" means gone; the vault is the owner's own notebook, and Thursday
        removing pages from it is not its call.
        """
        if not self.enabled:
            return None
        path = self.root / relative
        if not path.exists():
            return None
        destination = self.root / "09 Archive" / path.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            destination = destination.with_stem(f"{path.stem} {datetime.now(UTC):%Y%m%d%H%M%S}")
        path.rename(destination)
        log.info("obsidian_note_archived", path=str(destination))
        return destination

    def inbox(self, text: str, *, title: str | None = None) -> Path | None:
        """Somewhere for something that has no home yet — the point of 00 Inbox."""
        when = datetime.now(UTC)
        return self.write_note(
            folder="00 Inbox",
            title=title or f"{when:%Y-%m-%d %H%M} note",
            body=text,
            frontmatter={"type": "inbox", "captured": when.isoformat(timespec="seconds")},
            overwrite=False,
        )

    # ------------------------------------------------------------------ reading

    def search(self, query: str, *, limit: int = 20) -> list[tuple[Path, str]]:
        """Plain substring search over the vault — the fallback when nothing is indexed."""
        if not self.enabled or not self.root.exists():
            return []
        needle = query.lower()
        out: list[tuple[Path, str]] = []
        for path in sorted(self.root.rglob("*.md")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            index = text.lower().find(needle)
            if index >= 0:
                out.append((path, text[max(0, index - 80) : index + 160].replace("\n", " ")))
            if len(out) >= limit:
                break
        return out

    def read_note(self, relative: str) -> tuple[dict[str, Any], str] | None:
        path = self.root / relative
        if not path.exists():
            return None
        return _parse(path.read_text(encoding="utf-8"))


def _render(frontmatter: dict[str, Any], body: str) -> str:
    lines = ["---"]
    for key, value in frontmatter.items():
        if value is None:
            continue
        if isinstance(value, list):
            lines.append(f"{key}: [{', '.join(str(v) for v in value)}]")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines) + "\n\n" + body.rstrip() + "\n"


def _as_list(value: Any) -> list[str]:
    """Frontmatter round-trips lists as `[a, b]`; read either form back."""
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [v.strip() for v in value.strip("[]").split(",") if v.strip()]
    return []


def _parse(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    _, _, rest = text.partition("---\n")
    raw_meta, _, body = rest.partition("---\n")
    meta: dict[str, Any] = {}
    for line in raw_meta.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.strip()
    return meta, body.lstrip()
