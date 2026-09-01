"""Memory write policy, retrieval scoring and conflict handling (§7, §11)."""

from __future__ import annotations

import pytest

from thursday.memory.embeddings import HashEmbeddingProvider, cosine
from thursday.memory.manager import MemoryManager
from thursday.memory.vector import InMemoryVectorStore
from thursday.shared.enums import DataSensitivity, MemoryLayer, MemorySource
from thursday.shared.models import MemoryQuery, MemoryWrite


@pytest.fixture
def memory() -> MemoryManager:
    return MemoryManager(embedder=HashEmbeddingProvider(256), vectors=InMemoryVectorStore())


async def test_small_talk_is_not_remembered(memory):
    for text in ("ครับ", "thanks", "ok", "สวัสดี"):
        assert await memory.write(
            MemoryWrite(layer=MemoryLayer.SEMANTIC, content=text, source=MemorySource.USER)
        ) is None


async def test_credential_material_is_never_written(memory):
    assert await memory.write(
        MemoryWrite(
            layer=MemoryLayer.SEMANTIC,
            content="the deploy key is sk-ant-api03-abcdefghijklmnopqrstuvwxyz",
            source=MemorySource.USER,
            importance=1.0,
        )
    ) is None


async def test_secret_classified_content_is_never_written(memory):
    assert await memory.write(
        MemoryWrite(
            layer=MemoryLayer.PREFERENCE,
            content="something quite ordinary",
            sensitivity=DataSensitivity.SECRET,
        )
    ) is None


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
    assert await memory.write(
        MemoryWrite(layer=MemoryLayer.PREFERENCE, content="anything at all")
    ) is None


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
            layer=MemoryLayer.PREFERENCE, key="report_format",
            content="ผู้ใช้ชอบรายงานเป็น PDF", source=MemorySource.USER, confidence=0.9,
        )
    )
    await memory.write(
        MemoryWrite(
            layer=MemoryLayer.PREFERENCE, key="report_format",
            content="ผู้ใช้ชอบรายงานเป็น Word", source=MemorySource.AGENT, confidence=0.5,
        )
    )
    conflicts = memory.conflicts()
    assert len(conflicts) == 1
    assert conflicts[0].resolution == "pending"
    # Both values survive so Thursday can report the contradiction rather than pick blindly.
    assert memory.stats()["preference"] == 2
    assert "PDF" in conflicts[0].old_value and "Word" in conflicts[0].new_value


async def test_a_stronger_source_supersedes_with_a_link_not_an_overwrite(memory):
    old = await memory.write(
        MemoryWrite(
            layer=MemoryLayer.PREFERENCE, key="deadline",
            content="ส่งงานวันศุกร์", source=MemorySource.AGENT, confidence=0.6,
        )
    )
    new = await memory.write(
        MemoryWrite(
            layer=MemoryLayer.PREFERENCE, key="deadline",
            content="ส่งงานวันพฤหัสบดี", source=MemorySource.USER, confidence=0.95,
        )
    )
    assert old is not None and new is not None
    assert new.supersedes_id == old.id
    assert (await memory.get(old.id)).superseded_by_id == new.id


async def test_recall_excludes_superseded_records_by_default(memory):
    await memory.write(
        MemoryWrite(layer=MemoryLayer.PREFERENCE, key="k", content="ค่าเดิม",
                    source=MemorySource.AGENT, confidence=0.5)
    )
    await memory.write(
        MemoryWrite(layer=MemoryLayer.PREFERENCE, key="k", content="ค่าใหม่",
                    source=MemorySource.USER, confidence=0.95)
    )
    hits = await memory.recall(MemoryQuery(text="ค่า", layers=[MemoryLayer.PREFERENCE], k=5))
    assert [h.content for h in hits] == ["ค่าใหม่"]


async def test_pinned_records_outrank_their_raw_score(memory):
    await memory.write(
        MemoryWrite(layer=MemoryLayer.SEMANTIC, content="โครงการ Alpha ใช้ Postgres",
                    source=MemorySource.USER, importance=0.7)
    )
    pinned = await memory.write(
        MemoryWrite(layer=MemoryLayer.SEMANTIC, content="เจ้าของชื่อ Supakit",
                    source=MemorySource.USER, importance=0.7, pinned=True)
    )
    hits = await memory.recall(MemoryQuery(text="โครงการ Alpha", k=2))
    assert pinned is not None
    assert any(h.id == pinned.id for h in hits)


async def test_working_memory_expires_and_is_swept(memory):
    from datetime import UTC, datetime, timedelta

    record = await memory.write(
        MemoryWrite(
            layer=MemoryLayer.WORKING, content="the file being processed is grades.xlsx",
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
