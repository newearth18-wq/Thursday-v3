"""The vault mirror (§8, V5).

Postgres is where Thursday remembers. Obsidian is where the *owner* remembers — a folder of
plain Markdown they can read, edit, search and take with them if Thursday is ever switched
off. The two serve different purposes, and mirroring everything from one into the other
would ruin the second: a vault with a note for every episodic trace is a vault nobody opens.

So the mirror is selective, and the rule it follows is a question about the reader rather
than about the data: *would a person, six months from now, be glad this was written down?*
That excludes working scratch and episodic chatter, and includes the durable layers.

It subscribes to the event bus rather than being called by the memory manager, so the
manager does not need to know the vault exists — and switching the vault off (§68) removes
a subscriber instead of leaving dead branches inside the write path.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from thursday_core.logging import get_logger
from thursday_shared.enums import MemoryLayer
from thursday_shared.errors import SecretLeakBlocked

log = get_logger(__name__)

#: Layers worth a page in the owner's own notebook. Episodic and working are deliberately
#: absent: they are how Thursday reconstructs what happened, not something a person reads.
MIRRORED_LAYERS: frozenset[MemoryLayer] = frozenset(
    {
        MemoryLayer.SEMANTIC,
        MemoryLayer.PROCEDURAL,
        MemoryLayer.PREFERENCE,
        MemoryLayer.PROJECT,
        MemoryLayer.KNOWLEDGE,
    }
)

#: Below this, a memory is a passing detail. The threshold is higher than the write policy's
#: because the bar for "worth storing" and "worth reading later" are not the same bar.
MIRROR_MIN_IMPORTANCE = 0.6


class VaultMirror:
    """Writes durable memories into the vault as notes.

    It reads the record back from the memory manager rather than from the event payload.
    The event carries an id and a layer, and putting the *content* on the bus instead would
    hand every subscriber the text of every memory — a wider exposure than this one
    subscriber needs, for no gain (§34).
    """

    def __init__(
        self, vault: Any, memory: Any, *, min_importance: float = MIRROR_MIN_IMPORTANCE
    ) -> None:
        self._vault = vault
        self._memory = memory
        self.min_importance = min_importance
        self.written = 0
        self.skipped = 0

    def attach(self, bus: Any) -> VaultMirror:
        bus.subscribe("memory.created", self.on_memory)
        bus.subscribe("memory.superseded", self.on_superseded)
        return self

    def should_mirror(self, layer: str, importance: float, pinned: bool) -> bool:
        try:
            resolved = MemoryLayer(layer)
        except ValueError:
            return False
        if resolved not in MIRRORED_LAYERS:
            return False
        # A pinned memory is one the owner said matters, which settles the question.
        return pinned or importance >= self.min_importance

    async def on_memory(self, event: Any) -> None:
        payload = getattr(event, "payload", {}) or {}
        memory_id = payload.get("memory_id")
        if not memory_id:
            return

        record = await self._memory.get(UUID(str(memory_id)))
        if record is None:
            # Written and immediately forgotten — "don't remember this" arriving between
            # the write and this handler. Nothing to mirror, and nothing wrong.
            self.skipped += 1
            return

        if not self.should_mirror(str(record.layer), record.importance, record.pinned):
            self.skipped += 1
            return

        content = record.content.strip()
        if not content:
            self.skipped += 1
            return

        try:
            path = self._vault.memory_note(
                memory_id=record.id,
                layer=str(record.layer),
                content=content,
                source=str(record.source),
                confidence=record.confidence,
            )
        except SecretLeakBlocked:
            # Defence in depth, not the first line: the memory manager redacts on the write
            # path, so a record reaching here should already be clean. Kept because the
            # vault's refusal must never propagate out of an event handler into whatever
            # published the event — a memory the vault will not hold is still a memory, and
            # the write that created it has already succeeded (§35).
            log.warning("vault_mirror_refused_secret", layer=str(record.layer))
            self.skipped += 1
            return
        except Exception as exc:
            log.warning("vault_mirror_failed", error=str(exc))
            self.skipped += 1
            return

        if path is not None:
            self.written += 1

    async def on_superseded(self, event: Any) -> None:
        """A memory that was replaced gets a note for the *new* value.

        The old note is left alone rather than rewritten. The vault is the owner's, and a
        history they can see beats a file that silently changes underneath them — Obsidian's
        own version history would show a mysterious edit with no author.
        """
        await self.on_memory(event)
