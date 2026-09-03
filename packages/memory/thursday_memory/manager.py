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
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from thursday_core.logging import get_logger
from thursday_core.persistence import NullRepository
from thursday_security.redaction import SecretRedactor
from thursday_shared.enums import (
    SOURCE_RANK,
    DataSensitivity,
    MemoryDecision,
    MemoryLayer,
    MemoryRelation,
    MemorySource,
)
from thursday_shared.models import (
    Event,
    MemoryCandidate,
    MemoryConflict,
    MemoryJudgement,
    MemoryLink,
    MemoryQuery,
    MemoryRecord,
    MemoryWrite,
    utcnow,
)

from thursday_memory.embeddings import cosine

log = get_logger(__name__)


class MemoryRestoreError(Exception):
    """Stored memories exist and none of them could be loaded."""


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

#: How much weight a claim carries by where it came from (§74). The owner stating something
#: outranks an agent's inference about the same subject even when the inference is more
#: confident in itself — confidence measures how sure the source is, not how much it is
#: worth believing.
SOURCE_TRUST: dict[MemorySource, float] = {
    MemorySource.USER: 1.0,
    MemorySource.FILE: 0.85,
    MemorySource.DATABASE: 0.85,
    MemorySource.EMAIL: 0.75,
    MemorySource.SENSOR: 0.7,
    MemorySource.CAMERA: 0.65,
    MemorySource.AGENT: 0.6,
    MemorySource.WEB: 0.5,
    MemorySource.INFERENCE: 0.5,
}


def _trigrams(text: str) -> set[str]:
    cleaned = "".join(ch for ch in text.lower() if not ch.isspace())
    return {cleaned[i : i + 3] for i in range(max(0, len(cleaned) - 2))}


def _lexical_overlap(subject: str, content: str) -> float:
    """How much of the subject literally appears in the content, 0-1.

    Character trigrams rather than words, because Thai is written without spaces: "ตาราง"
    inside "เป็นตารางก่อน" is a real mention, and any word-splitting approach would miss it.
    """
    wanted = _trigrams(subject)
    if not wanted:
        return 0.0
    return len(wanted & _trigrams(content)) / len(wanted)


#: Near-duplicate threshold. Above this, a write updates rather than adds.
DEDUPE_SIMILARITY = 0.95
#: New information may only supersede old on this much extra confidence (§7.4).
SUPERSEDE_CONFIDENCE_MARGIN = 0.15

#: The layers that describe *how Thursday should behave* rather than what is true. Only the
#: owner may write these (§110, PART 76): a standing instruction from anywhere else is an
#: instruction nobody gave, and it would shape work for months without announcing itself.
BEHAVIOUR_LAYERS = frozenset({MemoryLayer.PREFERENCE, MemoryLayer.PROCEDURAL})

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
        repository: Any = None,
    ) -> None:
        self._embedder = embedder
        self._vectors = vectors
        self._bus = bus
        self._redactor = redactor or SecretRedactor()
        self._working_ttl = timedelta(hours=working_ttl_hours)
        #: Where memories live between runs (Sprint 51). `_records` is an index over this,
        #: not a second store: it is loaded from the repository at startup and written
        #: through on every change. Two stores that can disagree are worse than one store
        #: and no persistence, because the disagreement is invisible.
        self._repository = repository or NullRepository()
        self._records: dict[UUID, MemoryRecord] = {}
        self._conflicts: list[MemoryConflict] = []
        #: PART 41 — typed edges between memories, kept instead of overwrites.
        self._links: list[MemoryLink] = []
        #: PART 39 — candidates the owner still has to confirm.
        self._pending_confirmation: list[MemoryCandidate] = []
        self.memory_disabled = False  # flipped by a privacy zone (PART 68)

    # ------------------------------------------------------------------ write policy

    def judge(self, candidate: MemoryCandidate) -> MemoryJudgement:
        """PART 39. STORE, TEMPORARY, IGNORE or ASK_USER — with the reason attached.

        The reason is logged and surfaced through the API, so the write policy is auditable
        rather than a black box the owner has to take on trust.
        """
        text = candidate.content.strip()

        # --- refusals: never stored, whatever else is true -----------------------
        if self.memory_disabled:
            return MemoryJudgement(
                decision=MemoryDecision.IGNORE, reason="memory is disabled by a privacy zone"
            )
        if candidate.sensitivity >= DataSensitivity.SECRET:
            return MemoryJudgement(
                decision=MemoryDecision.IGNORE, reason="payload is classified SECRET"
            )
        if self._redactor.scan(text):
            return MemoryJudgement(
                decision=MemoryDecision.IGNORE, reason="content matches a credential pattern"
            )
        if not text:
            return MemoryJudgement(decision=MemoryDecision.IGNORE, reason="empty content")
        if _SMALL_TALK.match(text):
            return MemoryJudgement(decision=MemoryDecision.IGNORE, reason="small talk")

        # --- PART 76 / §110: only the owner sets standing behaviour ---------------
        # A document Thursday read cannot redefine who the owner is, what they like, or how
        # they want work done. The proposal becomes a question instead of a fact.
        #
        # PROCEDURAL is in this list and was missing from it, which was the whole gap:
        # a procedural memory is the layer built to *shape later work* (V5), so a web page or
        # an agent writing one is a standing instruction nobody gave — precisely the
        # substitution §110 forbids. PREFERENCE was guarded and PROCEDURAL, which is the
        # layer that actually changes behaviour, was not.
        #
        # PROJECT is deliberately not here: a project memory is a fact about a project, and
        # agents are supposed to record those.
        if candidate.layer in BEHAVIOUR_LAYERS and candidate.source is not MemorySource.USER:
            return MemoryJudgement(
                decision=MemoryDecision.ASK_USER,
                reason=(
                    f"a {candidate.layer} memory proposed by "
                    f"{candidate.proposed_by or candidate.source} needs the owner's confirmation"
                ),
            )

        # --- durable ------------------------------------------------------------
        # Reached only for owner-sourced procedural writes; the guard above turned every
        # other source into a question.
        if candidate.layer in (MemoryLayer.PROCEDURAL, MemoryLayer.PROJECT):
            return MemoryJudgement(
                decision=MemoryDecision.STORE, reason=f"{candidate.layer} writes are durable"
            )
        if candidate.layer is MemoryLayer.PREFERENCE:
            return MemoryJudgement(
                decision=MemoryDecision.STORE, reason="the owner stated a preference"
            )
        if candidate.source is MemorySource.USER and _DURABLE_MARKERS.search(text):
            return MemoryJudgement(
                decision=MemoryDecision.STORE, reason="the owner asserted a durable fact"
            )
        if candidate.layer is MemoryLayer.EPISODIC and candidate.structured.get("outcome"):
            return MemoryJudgement(decision=MemoryDecision.STORE, reason="a completed task outcome")
        if candidate.importance >= 0.6:
            return MemoryJudgement(
                decision=MemoryDecision.STORE,
                reason=f"importance {candidate.importance:.2f} is above the threshold",
            )

        # --- uncertain: keep briefly, or ask ------------------------------------
        if candidate.layer is MemoryLayer.WORKING:
            return MemoryJudgement(
                decision=MemoryDecision.TEMPORARY,
                reason="task-scoped working memory",
                ttl_hours=self._working_ttl.total_seconds() / 3600,
            )
        if candidate.confidence < 0.5:
            return MemoryJudgement(
                decision=MemoryDecision.ASK_USER,
                reason=f"confidence {candidate.confidence:.2f} is too low to store silently",
            )
        return MemoryJudgement(
            decision=MemoryDecision.IGNORE,
            reason=(
                f"nothing durable in a {candidate.layer} write of importance "
                f"{candidate.importance:.2f}"
            ),
        )

    def should_write(
        self, write: MemoryWrite, *, is_small_talk: bool | None = None
    ) -> tuple[bool, str]:
        """Boolean view of :meth:`judge`, for callers that only need yes or no."""
        if is_small_talk:
            return False, "small talk"
        judgement = self.judge(_candidate_from(write))
        return judgement.stores, judgement.reason

    async def propose(
        self, candidate: MemoryCandidate
    ) -> tuple[MemoryJudgement, MemoryRecord | None]:
        """The PART 39 path: judge first, store only if the judgement says so.

        Returns the judgement as well as the record, so a caller can act on ASK_USER
        instead of reading a missing record as "nothing happened".
        """
        judgement = self.judge(candidate)
        if judgement.decision is MemoryDecision.ASK_USER:
            self._pending_confirmation.append(candidate)
            log.info("memory_needs_confirmation", reason=judgement.reason)
            await self._emit_candidate("memory.confirmation_required", candidate, judgement)
            return judgement, None
        if not judgement.stores:
            log.debug("memory_write_skipped", layer=str(candidate.layer), reason=judgement.reason)
            return judgement, None

        write = candidate.to_write()
        if judgement.decision is MemoryDecision.TEMPORARY and write.expires_at is None:
            write.expires_at = utcnow() + self._working_ttl
        return judgement, await self.write(write, force=True)

    def pending_confirmations(self) -> list[MemoryCandidate]:
        """Candidates waiting on the owner's yes or no (PART 39, PART 76)."""
        return list(self._pending_confirmation)

    async def confirm(self, index: int, *, accept: bool) -> MemoryRecord | None:
        """Resolve a pending candidate.

        Accepting makes the owner its source, which is exactly what gives a preference the
        authority it needs — and what an agent could not have given it.
        """
        if not 0 <= index < len(self._pending_confirmation):
            raise IndexError(index)
        candidate = self._pending_confirmation.pop(index)
        if not accept:
            return None
        candidate.source = MemorySource.USER
        candidate.confidence = max(candidate.confidence, 0.9)
        return await self.write(candidate.to_write(), force=True)

    async def write(
        self, write: MemoryWrite, *, force: bool = False, detect_conflicts: bool = True
    ) -> MemoryRecord | None:
        """Write a record, subject to the write policy.

        ``detect_conflicts=False`` is used by ``supersede``, whose caller has already
        resolved the contradiction — without it the new record would be compared against
        the very record it replaces and recurse.
        """
        allowed, reason = (True, "forced") if force else self.should_write(write)
        if not allowed:
            log.debug("memory_write_skipped", layer=str(write.layer), reason=reason)
            return None

        redacted = self._redactor.redact(write.content)
        embedding = (await self._embedder.embed([redacted.text]))[0]  # type: ignore[attr-defined]

        # Dedupe and conflict detection run against current records in the same layer.
        near = (
            self._nearest(embedding, layer=write.layer, key=write.key) if detect_conflicts else None
        )
        if near is not None:
            existing, similarity = near
            if (
                similarity >= DEDUPE_SIMILARITY
                and existing.content.strip() == redacted.text.strip()
            ):
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
            session_id=write.session_id,
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
        """Replace a record with a newer one, keeping the link between them (§11).

        The old record is retired *before* the new one is written so no retrieval can see
        both as current, and conflict detection is skipped because the contradiction that
        led here is already resolved.
        """
        old = self._records.get(old_id)
        if old is not None:
            old.superseded_by_id = None  # set once the replacement exists
            old.valid_to = utcnow()

        record = await self.write(new, force=True, detect_conflicts=False)
        if record is None:  # pragma: no cover - force=True always writes
            raise RuntimeError("supersede failed to write the replacement record")

        if old is not None:
            old.superseded_by_id = record.id
            record.supersedes_id = old.id
            self.link(record.id, old.id, MemoryRelation.SUPERSEDES, note="stronger source")
            await self._emit("memory.superseded", record, replaced=str(old.id))
        return record

    async def forget(self, memory_id: UUID) -> None:
        """Forgetting reaches the table too, or it is not forgetting (ADR 0019).

        A memory dropped from the index and left in storage comes back on the next restart,
        which is the failure mode the owner would least expect and least easily notice.
        """
        self._records.pop(memory_id, None)
        await self._repository.remove(memory_id)
        await self._vectors.delete([memory_id])  # type: ignore[attr-defined]

    async def forget_about(
        self, subject: str, *, threshold: float = 0.55, limit: int = 50
    ) -> list[MemoryRecord]:
        """Delete everything the owner meant by "forget about X".

        Deletion is the one memory operation with no undo, so the threshold is deliberately
        higher than a recall's: retrieving something marginally relevant is a small cost,
        and deleting something marginally relevant is not recoverable. What was removed is
        returned rather than counted, so the reply can name it and the owner can tell
        immediately if the match was too wide.
        """
        if not subject.strip():
            return []

        vector = (await self._embedder.embed([subject]))[0]  # type: ignore[attr-defined]

        def relevance(record: MemoryRecord) -> float:
            """Similarity, or a literal overlap — whichever is stronger.

            For deletion the lexical signal is the *better* one. "Forget about the budget"
            means forget the things that mention the budget, and a record that literally
            says so is a certain match, where a vector is a guess. It also keeps this
            working on the offline embedder, whose paraphrase similarity is weak — without
            it, "forget X" would quietly match nothing whenever Thursday is offline, which
            is the worst possible failure for a privacy operation.
            """
            return max(
                cosine(vector, record.embedding or []), _lexical_overlap(subject, record.content)
            )

        matches = [
            record
            for record in list(self._records.values())
            if record.is_current and relevance(record) >= threshold
        ]
        matches.sort(key=relevance, reverse=True)

        removed: list[MemoryRecord] = []
        for record in matches[:limit]:
            await self.forget(record.id)
            removed.append(record)
        if removed:
            log.info("memory_forgotten_on_request", subject=subject[:40], count=len(removed))
        return removed

    async def forget_from_session(self, session_id: UUID) -> list[MemoryRecord]:
        """Remove what one conversation wrote.

        The other half of "don't remember this": stopping future writes would leave the
        thing the owner was pointing at still stored, which is the opposite of what they
        asked. Scoped to the session rather than to a time window, so it cannot reach into
        a different conversation that happened to be running alongside.
        """
        doomed = [r for r in list(self._records.values()) if r.session_id == session_id]
        for record in doomed:
            await self.forget(record.id)
        return doomed

    # ------------------------------------------------------------------ backup (Sprint 47)

    def export_state(self) -> list[dict]:
        """Every stored memory as plain data, for a backup.

        A method here rather than a backup module reading `_records`: the shape of this state
        is this class's business, and a backup that knows the shape breaks quietly the first
        time the class changes.
        """
        return [record.model_dump(mode="json") for record in self._records.values()]

    def import_state(self, rows: list[dict], *, replace: bool = True) -> int:
        """Load memories back. Returns how many were restored.

        Restored as-is, without re-running `judge`. A restore is not a new observation — the
        decision to keep each of these was already made, and re-judging them would let a
        policy change since the backup silently discard things the owner still has.
        """
        if replace:
            self._records.clear()
        for row in rows:
            record = MemoryRecord.model_validate(row)
            self._records[record.id] = record
        return len(rows)

    async def restore(self) -> int:
        """Load what was kept, at startup. Returns how many came back.

        Vectors are rebuilt from the stored embeddings rather than recomputed: re-embedding
        on every boot would be slow, and worse, a change of embedding model would silently
        re-score every memory the owner has.
        """
        rows = await self._repository.load()
        restored = 0
        unreadable = 0
        for row in rows:
            try:
                record = MemoryRecord.model_validate(row)
            except ValidationError as exc:
                # One unreadable row must not cost the owner every other memory.
                log.warning("memory_row_unreadable", error=str(exc))
                unreadable += 1
                continue
            self._records[record.id] = record
            restored += 1

        if restored:
            await self._vectors.upsert(  # type: ignore[attr-defined]
                [(r.id, r.embedding or [], {"layer": str(r.layer)}) for r in self._records.values()]
            )
            log.info("memory_restored", records=restored)
        if rows and not restored:
            # Not "nothing to restore". Every row that was there failed to load, and a
            # startup line reading `memories=0` looks identical to a first boot — which is
            # how somebody spends a week not noticing their assistant has amnesia.
            raise MemoryRestoreError(
                f"{unreadable} stored memories could not be read; refusing to start as "
                "though there were none"
            )
        return restored

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
            record.score = self._score(record, sim, now, query)
            scored.append(record)

        scored.sort(key=lambda r: r.score or 0.0, reverse=True)
        top = scored[: query.k]
        for record in top:
            record.access_count += 1
            record.last_accessed_at = now
        return top

    def _score(
        self, record: MemoryRecord, similarity: float, now: datetime, query: MemoryQuery
    ) -> float:
        """§7's retrieval score: similarity, recency, importance, project relevance and
        source confidence, plus how often this memory has actually proved useful.

        The two weakest signals are deliberately the ones that decay: recency is worthless
        for a preference the owner stated once and still holds, which is why the half-life
        table gives `SEMANTIC` and `PREFERENCE` an infinite one.
        """
        half_life = HALF_LIFE_DAYS.get(record.layer, 45.0)
        age_days = max(0.0, (now - record.created_at).total_seconds() / 86400)
        recency = 1.0 if math.isinf(half_life) else math.exp(-age_days / half_life)
        usage = min(1.0, record.access_count / 10)

        # Project relevance. A soft preference, not a filter: asked how these reports are
        # usually written, this project's answer should come first — but a general habit
        # is still a real answer, and excluding it would hide the thing that shaped it.
        wanted = query.prefer_project_id or query.project_id
        if wanted is None:
            relevance = 0.5  # nothing to prefer; neutral rather than a penalty for all
        elif record.project_id == wanted:
            relevance = 1.0
        elif record.project_id is None:
            relevance = 0.6  # general knowledge still applies to this project
        else:
            relevance = 0.0  # belongs to a different project

        # Source confidence: *who* asserted it, weighted, times how sure they were. The
        # owner saying something outranks an agent's inference about the same subject even
        # when the inference is more confident in itself.
        trust = SOURCE_TRUST.get(record.source, 0.6)
        source_confidence = trust * record.confidence

        score = (
            0.30 * similarity
            + 0.15 * recency
            + 0.18 * record.importance
            + 0.15 * relevance
            + 0.14 * source_confidence
            + 0.08 * usage
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
        return not (record.expires_at and record.expires_at <= utcnow())

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
        return bool(write.key and existing.key == write.key) or similarity >= 0.90

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
        self.link(record.id, existing.id, MemoryRelation.CONTRADICTS, note=conflict.describe())
        log.warning("memory_conflict", detail=conflict.describe())
        await self._emit("memory.conflict", record, conflict=conflict.describe())
        return record

    def link(
        self, from_id: UUID, to_id: UUID, relation: MemoryRelation, *, note: str = ""
    ) -> MemoryLink:
        """PART 41 — record *how* two memories relate, instead of overwriting one."""
        edge = MemoryLink(from_id=from_id, to_id=to_id, relation=relation, note=note)
        self._links.append(edge)
        return edge

    def links(self, memory_id: UUID | None = None) -> list[MemoryLink]:
        if memory_id is None:
            return list(self._links)
        return [edge for edge in self._links if memory_id in (edge.from_id, edge.to_id)]

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
        """The one place a memory is kept. Persisted before it is indexed.

        In that order deliberately, and the exception is not caught: a `remember` that
        returned a record it failed to store is a lie the owner discovers after a restart,
        when there is nothing to be done about it. Failing here means they hear about it
        while the thing they said is still on the screen.
        """
        await self._repository.put(record.model_dump(mode="python"))
        self._records[record.id] = record
        await self._vectors.upsert(  # type: ignore[attr-defined]
            [(record.id, record.embedding or [], {"layer": str(record.layer)})]
        )

    async def _emit_candidate(
        self, kind: str, candidate: MemoryCandidate, judgement: MemoryJudgement
    ) -> None:
        if self._bus is None:
            return
        await self._bus.publish(  # type: ignore[attr-defined]
            Event(
                kind=kind,
                task_id=candidate.task_id,
                payload={
                    "content": candidate.content[:200],
                    "layer": str(candidate.layer),
                    "decision": judgement.decision.value,
                    "reason": judgement.reason,
                },
            )
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


def _candidate_from(write: MemoryWrite) -> MemoryCandidate:
    """Adapt the older write shape onto the PART 39 candidate the judge expects."""
    return MemoryCandidate(
        content=write.content,
        layer=write.layer,
        key=write.key,
        structured=write.structured,
        importance=write.importance,
        confidence=write.confidence,
        source=write.source,
        source_ref=write.source_ref,
        sensitivity=write.sensitivity,
        project_id=write.project_id,
        task_id=write.task_id,
        pinned=write.pinned,
        expires_at=write.expires_at,
    )
