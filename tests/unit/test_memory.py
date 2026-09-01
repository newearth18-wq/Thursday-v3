"""Memory write policy, retrieval scoring and conflict handling (§7, §11)."""

from __future__ import annotations

import pytest
from thursday_memory.embeddings import HashEmbeddingProvider, cosine
from thursday_memory.manager import MemoryManager
from thursday_memory.vector import InMemoryVectorStore
from thursday_shared.enums import DataSensitivity, MemoryLayer, MemorySource
from thursday_shared.models import MemoryQuery, MemoryWrite


@pytest.fixture
def memory() -> MemoryManager:
    return MemoryManager(embedder=HashEmbeddingProvider(256), vectors=InMemoryVectorStore())


async def test_small_talk_is_not_remembered(memory):
    for text in ("ครับ", "thanks", "ok", "สวัสดี"):
        assert (
            await memory.write(
                MemoryWrite(layer=MemoryLayer.SEMANTIC, content=text, source=MemorySource.USER)
            )
            is None
        )


async def test_credential_material_is_never_written(memory):
    assert (
        await memory.write(
            MemoryWrite(
                layer=MemoryLayer.SEMANTIC,
                content="the deploy key is sk-ant-api03-abcdefghijklmnopqrstuvwxyz",
                source=MemorySource.USER,
                importance=1.0,
            )
        )
        is None
    )


async def test_secret_classified_content_is_never_written(memory):
    assert (
        await memory.write(
            MemoryWrite(
                layer=MemoryLayer.PREFERENCE,
                content="something quite ordinary",
                sensitivity=DataSensitivity.SECRET,
            )
        )
        is None
    )


async def test_durable_user_statements_are_remembered(memory):
    record = await memory.write(
        MemoryWrite(
            layer=MemoryLayer.SEMANTIC,
            content="from now on, send me reports as PDF",
            source=MemorySource.USER,
        )
    )
    assert record is not None
    assert record.layer is MemoryLayer.SEMANTIC


async def test_a_memory_disabled_zone_stops_all_writes(memory):
    memory.memory_disabled = True
    assert (
        await memory.write(MemoryWrite(layer=MemoryLayer.PREFERENCE, content="anything at all"))
        is None
    )


async def test_identical_content_deduplicates_rather_than_accumulating(memory):
    write = MemoryWrite(
        layer=MemoryLayer.PREFERENCE, key="tone", content="ผู้ใช้ชอบคำตอบสั้น", source=MemorySource.USER
    )
    first = await memory.write(write)
    second = await memory.write(write)
    assert first is not None and second is not None
    assert first.id == second.id
    assert memory.stats()["preference"] == 1


async def test_a_weaker_source_does_not_overwrite_a_stronger_one(memory):
    await memory.write(
        MemoryWrite(
            layer=MemoryLayer.SEMANTIC,
            key="deadline",
            content="กำหนดส่งคือวันศุกร์",
            source=MemorySource.USER,
            confidence=0.9,
            importance=0.8,
        )
    )
    await memory.write(
        MemoryWrite(
            layer=MemoryLayer.SEMANTIC,
            key="deadline",
            content="กำหนดส่งคือวันพุธ",
            source=MemorySource.AGENT,
            confidence=0.5,
            importance=0.8,
        )
    )
    conflicts = memory.conflicts()
    assert len(conflicts) == 1
    assert conflicts[0].resolution == "pending"
    # Both values survive, so Thursday can report the contradiction rather than pick blindly.
    assert memory.stats()["semantic"] == 2
    assert "ศุกร์" in conflicts[0].old_value and "พุธ" in conflicts[0].new_value

    # PART 41 — the relationship is recorded, not implied.
    from thursday_shared.enums import MemoryRelation

    assert any(edge.relation is MemoryRelation.CONTRADICTS for edge in memory.links())


async def test_a_stronger_source_supersedes_with_a_link_not_an_overwrite(memory):
    from thursday_shared.enums import MemoryRelation

    old = await memory.write(
        MemoryWrite(
            layer=MemoryLayer.SEMANTIC,
            key="deadline",
            content="ส่งงานวันศุกร์",
            source=MemorySource.AGENT,
            confidence=0.6,
            importance=0.8,
        )
    )
    new = await memory.write(
        MemoryWrite(
            layer=MemoryLayer.SEMANTIC,
            key="deadline",
            content="ส่งงานวันพฤหัสบดี",
            source=MemorySource.USER,
            confidence=0.95,
            importance=0.8,
        )
    )
    assert old is not None and new is not None
    assert new.supersedes_id == old.id
    assert (await memory.get(old.id)).superseded_by_id == new.id
    assert any(edge.relation is MemoryRelation.SUPERSEDES for edge in memory.links(new.id))


# ------------------------------------------------------------------ PART 39 / PART 76


async def test_the_judge_has_four_answers_not_two(memory):
    """PART 39 — 'should I remember this?' has four honest answers."""
    from thursday_shared.enums import MemoryDecision
    from thursday_shared.models import MemoryCandidate

    cases = {
        MemoryDecision.IGNORE: MemoryCandidate(content="ครับ", source=MemorySource.USER),
        MemoryDecision.STORE: MemoryCandidate(content="งาน Open House จัดวันที่ 12", importance=0.8),
        MemoryDecision.TEMPORARY: MemoryCandidate(
            content="กำลังประมวลผล grades.xlsx", layer=MemoryLayer.WORKING
        ),
        MemoryDecision.ASK_USER: MemoryCandidate(content="อาจจะประชุมวันอังคาร", confidence=0.3),
    }
    for expected, candidate in cases.items():
        assert memory.judge(candidate).decision is expected, candidate.content


async def test_an_agent_cannot_write_the_owners_preferences(memory):
    """PART 76 — a document Thursday read may not redefine what the owner likes."""
    from thursday_shared.enums import MemoryDecision
    from thursday_shared.models import MemoryCandidate

    judgement, record = await memory.propose(
        MemoryCandidate(
            content="ผู้ใช้ชอบรายงานเป็น Word",
            layer=MemoryLayer.PREFERENCE,
            source=MemorySource.AGENT,
            proposed_by="document-agent",
        )
    )
    assert judgement.decision is MemoryDecision.ASK_USER
    assert record is None
    assert memory.stats().get("preference", 0) == 0
    assert len(memory.pending_confirmations()) == 1


async def test_the_owner_may_state_a_preference_directly(memory):
    from thursday_shared.enums import MemoryDecision
    from thursday_shared.models import MemoryCandidate

    judgement, record = await memory.propose(
        MemoryCandidate(
            content="ผู้ใช้ชอบรายงานเป็น PDF",
            layer=MemoryLayer.PREFERENCE,
            source=MemorySource.USER,
        )
    )
    assert judgement.decision is MemoryDecision.STORE
    assert record is not None


async def test_confirming_a_candidate_makes_the_owner_its_source(memory):
    """Accepting is what gives a preference the authority an agent could not give it."""
    from thursday_shared.models import MemoryCandidate

    await memory.propose(
        MemoryCandidate(
            content="ผู้ใช้ชอบรายงานเป็น Word",
            layer=MemoryLayer.PREFERENCE,
            source=MemorySource.AGENT,
        )
    )
    record = await memory.confirm(0, accept=True)
    assert record is not None
    assert record.source is MemorySource.USER
    assert memory.pending_confirmations() == []


async def test_declining_a_candidate_stores_nothing(memory):
    from thursday_shared.models import MemoryCandidate

    await memory.propose(MemoryCandidate(content="อาจจะประชุมวันอังคาร", confidence=0.2))
    assert await memory.confirm(0, accept=False) is None
    assert memory.stats().get("semantic", 0) == 0
    assert memory.pending_confirmations() == []


async def test_recall_excludes_superseded_records_by_default(memory):
    await memory.write(
        MemoryWrite(
            layer=MemoryLayer.PREFERENCE,
            key="k",
            content="ค่าเดิม",
            source=MemorySource.AGENT,
            confidence=0.5,
        )
    )
    await memory.write(
        MemoryWrite(
            layer=MemoryLayer.PREFERENCE,
            key="k",
            content="ค่าใหม่",
            source=MemorySource.USER,
            confidence=0.95,
        )
    )
    hits = await memory.recall(MemoryQuery(text="ค่า", layers=[MemoryLayer.PREFERENCE], k=5))
    assert [h.content for h in hits] == ["ค่าใหม่"]


async def test_pinned_records_outrank_their_raw_score(memory):
    await memory.write(
        MemoryWrite(
            layer=MemoryLayer.SEMANTIC,
            content="โครงการ Alpha ใช้ Postgres",
            source=MemorySource.USER,
            importance=0.7,
        )
    )
    pinned = await memory.write(
        MemoryWrite(
            layer=MemoryLayer.SEMANTIC,
            content="เจ้าของชื่อ Supakit",
            source=MemorySource.USER,
            importance=0.7,
            pinned=True,
        )
    )
    hits = await memory.recall(MemoryQuery(text="โครงการ Alpha", k=2))
    assert pinned is not None
    assert any(h.id == pinned.id for h in hits)


async def test_working_memory_expires_and_is_swept(memory):
    from datetime import UTC, datetime, timedelta

    record = await memory.write(
        MemoryWrite(
            layer=MemoryLayer.WORKING,
            content="the file being processed is grades.xlsx",
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
    )
    assert record is not None
    assert await memory.recall(MemoryQuery(text="grades", layers=[MemoryLayer.WORKING])) == []
    assert await memory.decay() == 1


def test_the_hash_embedder_is_deterministic_and_separates_topics():
    import asyncio

    embedder = HashEmbeddingProvider(256)
    a, b, c = asyncio.run(
        embedder.embed(["เปิดไฟล์คะแนนนักเรียน", "เปิดไฟล์คะแนนนักเรียน", "พยากรณ์อากาศวันนี้"])
    )
    assert cosine(a, b) == pytest.approx(1.0, abs=1e-9)
    assert cosine(a, c) < 0.5
