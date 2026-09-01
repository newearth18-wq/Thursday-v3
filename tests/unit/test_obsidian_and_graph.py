"""The human-readable brain and the knowledge graph (§8, §10, §55)."""

from __future__ import annotations

from itertools import pairwise

import pytest

from thursday.memory.graph import KnowledgeGraph
from thursday.memory.obsidian import FOLDERS, ObsidianVault, safe_filename
from thursday.shared.errors import SecretLeakBlocked
from thursday.shared.ids import new_id


@pytest.fixture
def vault(tmp_path) -> ObsidianVault:
    v = ObsidianVault(tmp_path / "vault")
    v.ensure_structure()
    return v


def test_the_vault_structure_matches_the_documented_layout(vault):
    for folder in FOLDERS:
        assert (vault.root / folder).is_dir()


def test_a_note_carries_frontmatter_that_links_it_back(vault):
    memory_id = new_id()
    path = vault.memory_note(
        memory_id=memory_id,
        layer="semantic",
        content="โครงการ Alpha ใช้ pgvector",
        source="user",
        confidence=0.9,
    )
    text = path.read_text(encoding="utf-8")
    assert f"thursday_id: {memory_id}" in text
    assert "layer: semantic" in text
    assert "confidence: 0.9" in text

    meta, body = vault.read_note(str(path.relative_to(vault.root)))
    assert meta["source"] == "user"
    assert "pgvector" in body


def test_the_vault_refuses_credential_material_outright(vault):
    """§8 — redacting is not enough; a secret must never reach a plaintext vault at all."""
    with pytest.raises(SecretLeakBlocked):
        vault.write_note(
            folder="00 Inbox", title="keys", body="OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstu"
        )
    with pytest.raises(SecretLeakBlocked):
        vault.write_note(folder="00 Inbox", title="ghp_abcdefghijklmnopqrstuvwx", body="fine")
    assert list((vault.root / "00 Inbox").iterdir()) == []


def test_a_decision_entry_records_the_reasoning_not_just_the_choice(vault):
    path = vault.decision_log(
        decision="ใช้ pgvector แทน vector database แยก",
        reason="ให้ memory กับ metadata อยู่ในทรานแซกชันเดียวกัน",
        alternatives=["Qdrant", "Chroma"],
        source="architecture review",
        impact="ลดโอกาสที่ข้อมูลสองฝั่งจะ drift",
    )
    text = path.read_text(encoding="utf-8")
    assert "type: decision" in text
    for expected in ("Qdrant", "Chroma", "architecture review", "drift"):
        assert expected in text


def test_a_daily_note_appends_rather_than_overwriting(vault):
    from datetime import UTC, datetime

    when = datetime(2026, 3, 7, 9, 30, tzinfo=UTC)
    vault.daily_note("เปิด Chrome บน Office-PC", when=when)
    path = vault.daily_note("สร้างรายงานเสร็จ", when=when.replace(hour=17))
    text = path.read_text(encoding="utf-8")
    assert "Chrome" in text and "รายงาน" in text
    assert len(list((vault.root / "08 Daily").iterdir())) == 1


def test_search_finds_notes_by_content(vault):
    vault.write_note(folder="03 Knowledge", title="Open House", body="งาน Open House จัดวันที่ 12")
    hits = vault.search("Open House")
    assert hits and "Open House" in hits[0][1]


def test_filenames_are_made_safe_without_becoming_meaningless():
    assert safe_filename("report: Q1/Q2 <draft>") == "report- Q1-Q2 -draft-"
    assert safe_filename("") == "untitled"
    assert safe_filename("รายงานคะแนน") == "รายงานคะแนน"


def test_a_disabled_vault_is_a_no_op_not_an_error(tmp_path):
    disabled = ObsidianVault(tmp_path / "none", enabled=False)
    disabled.ensure_structure()
    assert disabled.write_note(folder="00 Inbox", title="t", body="b") is None
    assert disabled.search("anything") == []


# ------------------------------------------------------------------ knowledge graph


def test_the_graph_answers_a_two_hop_question():
    """'Which file did I use in the last meeting with this person?' (§10)."""
    graph = KnowledgeGraph()
    person = graph.upsert_entity(kind="person", name="อาจารย์สมชาย")
    meeting = graph.upsert_entity(kind="event", name="ประชุมวางแผน Open House")
    document = graph.upsert_entity(kind="document", name="openhouse-plan.xlsx")
    graph.relate(person, meeting, "attended")
    graph.relate(meeting, document, "used")

    results = graph.traverse(person.id, hops=2, target_kind="document")
    assert [(e.name, distance, path) for e, distance, path in results] == [
        ("openhouse-plan.xlsx", 2, ["attended", "used"])
    ]


def test_upsert_merges_attributes_instead_of_duplicating():
    graph = KnowledgeGraph()
    graph.upsert_entity(kind="project", name="Alpha", status="active")
    graph.upsert_entity(kind="project", name="Alpha", owner="Supakit")
    entity = graph.find("Alpha")
    assert entity is not None
    assert entity.attributes == {"status": "active", "owner": "Supakit"}
    assert graph.stats()["entities"] == 1


def test_traversal_is_bounded_by_the_hop_limit():
    graph = KnowledgeGraph()
    chain = [graph.upsert_entity(kind="object", name=f"n{i}") for i in range(5)]
    for a, b in pairwise(chain):
        graph.relate(a, b, "next")
    assert len(graph.traverse(chain[0].id, hops=1)) == 1
    assert len(graph.traverse(chain[0].id, hops=3)) == 3


def test_relationships_are_traversable_in_both_directions():
    graph = KnowledgeGraph()
    person = graph.upsert_entity(kind="person", name="Nina")
    project = graph.upsert_entity(kind="project", name="Beta")
    graph.relate(person, project, "owns")
    assert [e.name for _, e in graph.neighbours(project.id)] == ["Nina"]
