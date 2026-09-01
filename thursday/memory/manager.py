"""Memory Manager (§7, §11).

Two rules shape this module:

* A vector database is not a memory system. Layers, sources, confidence and supersession
  are first-class; embeddings are one index among several.
* Not every message is a memory. ``should_write`` is a gate, not a formality — an assistant
  that stores everything cannot retrieve anything.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

from thursday.core.logging import get_logger
from thursday.memory.embeddings import cosine
from thursday.security.redaction import SecretRedactor
from thursday.shared.enums import SOURCE_RANK, DataSensitivity, MemoryLayer, MemorySource
from thursday.shared.models import (
    Event,
    MemoryConflict,
    MemoryQuery,
    MemoryRecord,
    MemoryWrite,
    utcnow,
)

log = get_logger(__name__)

#: Half-life in days for the recency term of the retrieval score (§7.4).
HALF_LIFE_DAYS: dict[MemoryLayer, float] = {
    MemoryLayer.WORKING: 0.5,
    MemoryLayer.EPISODIC: 45.0,
    MemoryLayer.SEMANTIC: math.inf,
    MemoryLayer.PREFERENCE: math.inf,
    MemoryLayer.PROCEDURAL: 180.0,
    MemoryLayer.PROJECT: 120.0,
    MemoryLayer.KNOWLEDGE: math.inf,
}

#: Near-duplicate threshold. Above this, a write updates rather than adds.
DEDUPE_SIMILARITY = 0.95
#: New information may only supersede old on this much extra confidence (§7.4).
SUPERSEDE_CONFIDENCE_MARGIN = 0.15

_DURABLE_MARKERS = re.compile(
    r"(?i)(always|never|from now on|remember that|ต่อไปนี้|ให้จำไว้|จำไว้ว่า|ทุกครั้ง|"
    r"เรียกฉันว่า|call me|i prefer|ฉันชอบ|ผมชอบ|ห้าม|my .* is|ของฉันคือ)"
)
_SMALL_TALK = re.compile(
    r"(?i)^\s*(hi|hello|hey|thanks|thank you|ok|okay|sure|cool|สวัสดี|ขอบคุณ|ครับ|ค่ะ|โอเค)\b\W*$"
)


class MemoryManager:
    """Layered memory over a vector store plus structured lookup."""

    def __init__(
        self,
        *,
        embedder: object,
        vectors: object,
        bus: object | None = None,
        redactor: SecretRedactor | None = None,
        working_ttl_hours: int = 24,
    ) -> None:
        self._embedder = embedder
        self._vectors = vectors
        self._bus = bus
        self._redactor = redactor or SecretRedactor()
        self._working_ttl = timedelta(hours=working_ttl_hours)
        self._records: dict[UUID, MemoryRecord] = {}
        self._conflicts: list[MemoryConflict] = []
        self.memory_disabled = False  # flipped by a privacy zone (§68)

    # ------------------------------------------------------------------ write policy

    def should_write(self, write: MemoryWrite, *, is_small_talk: bool | None = None) -> tuple[bool, str]:
        """Return (decision, reason). Reasons are logged so the policy stays auditable."""
        if self.memory_disabled:
            return False, "memory is disabled by an active privacy zone"
        if write.sensitivity >= DataSensitivity.SECRET:
            return False, "payload is classified SECRET"
        if not self._redactor.scan(write.content) == ():
            return False, "content matches a credential pattern"
        text = write.content.strip()
        if not text:
            return False, "empty content"
        if is_small_talk or _SMALL_TALK.match(text):
            return False, "small talk"

        if write.layer in (MemoryLayer.PREFERENCE, MemoryLayer.PROCEDURAL, MemoryLayer.PROJECT):
            return True, f"{write.layer} writes are always durable"
        if write.source is MemorySource.USER and _DURABLE_MARKERS.search(text):
            return True, "user asserted a durable fact or preference"
        if write.layer is MemoryLayer.EPISODIC and write.structured.get("outcome"):
            return True, "completed task outcome"
        if write.importance >= 0.6:
            return True, f"importance {write.importance:.2f} is above threshold"
        if write.layer is MemoryLayer.WORKING:
            return True, "task-scoped working memory"
        return False, f"nothing durable in a {write.layer} write of importance {write.importance:.2f}"

    async def write(self, write: MemoryWrite, *, force: bool = False) -> MemoryRecord | None:
        allowed, reason = (True, "forced") if force else self.should_write(write)
        if not allowed:
            log.debug("memory_write_skipped", layer=str(write.layer), reason=reason)
            return None

        redacted = self._redactor.redact(write.content)
        embedding = (await self._embedder.embed([redacted.text]))[0]  # type: ignore[attr-defined]

        # Dedupe and conflict detection run against current records in the same layer.
        near = self._nearest(embedding, layer=write.layer, key=write.key)
        if near is not None:
            existing, similarity = near
            if similarity >= DEDUPE_SIMILARITY and existing.content.strip() == redacted.text.strip():
                existing.access_count += 1
                existing.confidence = max(existing.confidence, write.confidence)
                existing.last_accessed_at = utcnow()
                return existing
            if self._is_conflict(existing, write, similarity):
                return await self._handle_conflict(existing, write, embedding, reason)

        record = MemoryRecord(
            layer=write.layer,
            key=write.key,
            content=redacted.text,
            structured=write.structured,
            importance=write.importance,
            confidence=write.confidence,
            source=write.source,
            source_ref=write.source_ref,
            project_id=write.project_id,
            task_id=write.task_id,
            sensitivity=write.sensitivity,
            pinned=write.pinned,
            expires_at=write.expires_at
            or (utcnow() + self._working_ttl if write.layer is MemoryLayer.WORKING else None),
            embedding=embedding,
        )
        await self._store(record)
        log.info("memory_written", layer=str(record.layer), reason=reason, id=str(record.id))
        await self._emit("memory.created", record)
        return record

    async def supersede(self, old_id: UUID, new: MemoryWrite) -> MemoryRecord:
        old = self._records.get(old_id)
        record = await self.write(new, force=True)
        assert record is not None
        if old is not None:
            old.superseded_by_id = record.id
            old.valid_to = utcnow()
            record.supersedes_id = old.id
            await self._emit("memory.superseded", record, replaced=str(old.id))
        return record

    async def forget(self, memory_id: UUID) -> None:
        self._records.pop(memory_id, None)
        await self._vectors.delete([memory_id])  # type: ignore[attr-defined]

    async def get(self, memory_id: UUID) -> MemoryRecord | None:
        return self._records.get(memory_id)

    # ------------------------------------------------------------------ retrieval

    async def recall(self, query: MemoryQuery) -> list[MemoryRecord]:
        candidates = [
            r
            for r in self._records.values()
            if self._matches_filters(r, query) and (query.include_superseded or r.is_current)
        ]
        if not candidates:
            return []

        similarity: dict[UUID, float] = {}
        if query.text:
            vector = (await self._embedder.embed([query.text]))[0]  # type: ignore[attr-defined]
            for record in candidates:
                similarity[record.id] = cosine(vector, record.embedding or [])

        now = utcnow()
        scored: list[MemoryRecord] = []
        for record in candidates:
            sim = similarity.get(record.id, 0.0)
            record.score = self._score(record, sim, now)
            scored.append(record)

        scored.sort(key=lambda r: r.score or 0.0, reverse=True)
        top = scored[: query.k]
        for record in top:
            record.access_count += 1
            record.last_accessed_at = now
        return top

    def _score(self, record: MemoryRecord, similarity: float, now: datetime) -> float:
        half_life = HALF_LIFE_DAYS.get(record.layer, 45.0)
        age_days = max(0.0, (now - record.created_at).total_seconds() / 86400)
        recency = 1.0 if math.isinf(half_life) else math.exp(-age_days / half_life)
        usage = min(1.0, record.access_count / 10)
        score = (
            0.35 * similarity
            + 0.20 * recency
            + 0.20 * record.importance
            + 0.15 * record.confidence
            + 0.10 * usage
        )
        return min(1.0, score + 0.15) if record.pinned else score

    def _matches_filters(self, record: MemoryRecord, query: MemoryQuery) -> bool:
        if query.layers and record.layer not in query.layers:
            return False
        if query.project_id and record.project_id != query.project_id:
            return False
        if query.task_id and record.task_id != query.task_id:
            return False
        if query.key and record.key != query.key:
            return False
        if record.confidence < query.min_confidence:
            return False
        if record.expires_at and record.expires_at <= utcnow():
            return False
        return True

    # ------------------------------------------------------------------ conflicts (§11)

    def _nearest(
        self, embedding: list[float], *, layer: MemoryLayer, key: str | None
    ) -> tuple[MemoryRecord, float] | None:
        pool = [
            r
            for r in self._records.values()
            if r.layer is layer and r.is_current and (key is None or r.key == key)
        ]
        if not pool:
            return None
        best = max(pool, key=lambda r: cosine(embedding, r.embedding or []))
        return best, cosine(embedding, best.embedding or [])

    def _is_conflict(self, existing: MemoryRecord, write: MemoryWrite, similarity: float) -> bool:
        """Same subject, different assertion — a keyed pair, or a very close paraphrase."""
        if existing.content.strip() == write.content.strip():
            return False
        if write.key and existing.key == write.key:
            return True
        return similarity >= 0.90

    async def _handle_conflict(
        self, existing: MemoryRecord, write: MemoryWrite, embedding: list[float], reason: str
    ) -> MemoryRecord:
        conflict = MemoryConflict(
            memory_id=existing.id,
            key=write.key or existing.key,
            old_value=existing.content,
            new_value=write.content,
            old_source=existing.source,
            new_source=write.source,
            old_confidence=existing.confidence,
            new_confidence=write.confidence,
            old_observed_at=existing.created_at,
            new_observed_at=utcnow(),
        )

        higher_rank = SOURCE_RANK.get(write.source, 0) > SOURCE_RANK.get(existing.source, 0)
        confident_enough = write.confidence >= existing.confidence + SUPERSEDE_CONFIDENCE_MARGIN
        if higher_rank and confident_enough:
            conflict.resolution = "kept_new"
            self._conflicts.append(conflict)
            log.info("memory_auto_superseded", key=conflict.key, reason="stronger source")
            return await self.supersede(existing.id, write)

        # Otherwise both survive and Thursday reports the contradiction rather than
        # inventing a merged value.
        conflict.resolution = "pending"
        self._conflicts.append(conflict)
        record = MemoryRecord(
            layer=write.layer,
            key=write.key,
            content=self._redactor.redact(write.content).text,
            structured={**write.structured, "conflicts_with": str(existing.id)},
            importance=write.importance,
            confidence=write.confidence,
            source=write.source,
            source_ref=write.source_ref,
            project_id=write.project_id,
            task_id=write.task_id,
            sensitivity=write.sensitivity,
            embedding=embedding,
        )
        await self._store(record)
        log.warning("memory_conflict", detail=conflict.describe())
        await self._emit("memory.conflict", record, conflict=conflict.describe())
        return record

    def conflicts(self, *, pending_only: bool = True) -> list[MemoryConflict]:
        return [c for c in self._conflicts if not pending_only or c.resolution == "pending"]

    async def resolve_conflict(self, conflict_id: UUID, resolution: str) -> MemoryConflict:
        conflict = next((c for c in self._conflicts if c.id == conflict_id), None)
        if conflict is None:
            raise KeyError(conflict_id)
        conflict.resolution = resolution  # type: ignore[assignment]
        if resolution == "kept_old":
            for record in list(self._records.values()):
                if record.structured.get("conflicts_with") == str(conflict.memory_id):
                    await self.forget(record.id)
        return conflict

    # ------------------------------------------------------------------ maintenance

    async def decay(self) -> int:
        """Drop expired working memory. Long-term layers decay in score, not existence."""
        now = utcnow()
        expired = [
            r.id
            for r in self._records.values()
            if r.expires_at and r.expires_at <= now and not r.pinned
        ]
        for memory_id in expired:
            await self.forget(memory_id)
        return len(expired)

    def stats(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in self._records.values():
            counts[str(record.layer)] = counts.get(str(record.layer), 0) + 1
        counts["conflicts_pending"] = len(self.conflicts())
        return counts

    async def _store(self, record: MemoryRecord) -> None:
        self._records[record.id] = record
        await self._vectors.upsert(  # type: ignore[attr-defined]
            [(record.id, record.embedding or [], {"layer": str(record.layer)})]
        )

    async def _emit(self, kind: str, record: MemoryRecord, **extra: object) -> None:
        if self._bus is None:
            return
        await self._bus.publish(  # type: ignore[attr-defined]
            Event(
                kind=kind,
                task_id=record.task_id,
                payload={"memory_id": str(record.id), "layer": str(record.layer), **extra},
            )
        )
