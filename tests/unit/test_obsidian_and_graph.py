"""The human-readable brain and the knowledge graph (§8, §10, §55)."""

from __future__ import annotations

from itertools import pairwise

import pytest
from thursday_memory.graph import KnowledgeGraph
from thursday_memory.obsidian import FOLDERS, ObsidianVault, safe_filename
from thursday_shared.errors import SecretLeakBlocked
from thursday_shared.ids import new_id


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


# ------------------------------------------------------------------ the rest of the vault (V5)


def test_a_note_can_be_updated_without_losing_what_it_already_carried(vault):
    """An update that dropped `thursday_id` would sever the note from the thing it
    describes, which is the one piece of metadata nothing else can reconstruct."""
    path = vault.write_note(
        folder="03 Knowledge",
        title="Grading policy",
        body="original",
        frontmatter={"type": "memory", "thursday_id": "abc-123"},
    )
    relative = str(path.relative_to(vault.root))

    vault.update_note(relative, body="revised")
    meta, body = vault.read_note(relative)

    assert body.strip() == "revised"
    assert meta["thursday_id"] == "abc-123"
    assert meta["type"] == "memory"


def test_updating_a_missing_note_reports_rather_than_creating_one(vault):
    assert vault.update_note("03 Knowledge/nothing here.md", body="x") is None


def test_an_update_is_refused_if_it_would_carry_a_credential(vault):
    path = vault.write_note(folder="00 Inbox", title="Notes", body="fine")
    relative = str(path.relative_to(vault.root))
    with pytest.raises(SecretLeakBlocked):
        vault.update_note(relative, body="the token is ghp_0123456789abcdefghijklmnopqrstuvwxyzAB")


def test_notes_can_be_linked_and_linking_twice_changes_nothing(vault):
    a = vault.write_note(folder="01 Projects", title="Grade Report", body="the project")
    b = vault.write_note(folder="06 Decisions", title="Use a table", body="the decision")
    left, right = str(a.relative_to(vault.root)), str(b.relative_to(vault.root))

    vault.link_notes(left, right, relation="decided")
    vault.link_notes(left, right, relation="decided")

    _, body = vault.read_note(left)
    assert body.count("[[Use a table]]") == 1
    assert "## Links" in body


def test_linking_to_a_note_that_does_not_exist_is_refused(vault):
    a = vault.write_note(folder="01 Projects", title="Solo", body="x")
    assert vault.link_notes(str(a.relative_to(vault.root)), "01 Projects/ghost.md") is None


def test_tags_are_a_set(vault):
    path = vault.write_note(folder="03 Knowledge", title="Tagged", body="x")
    relative = str(path.relative_to(vault.root))

    vault.tag_note(relative, "grading", "#reports")
    vault.tag_note(relative, "reports", "policy")

    meta, _ = vault.read_note(relative)
    assert meta["tags"] == "[grading, policy, reports]"


def test_a_meeting_note_records_who_was_there(vault):
    path = vault.meeting_note(
        title="Term review", attendees=["Supakit", "the dean"], notes="agreed the format"
    )
    _, body = vault.read_note(str(path.relative_to(vault.root)))
    assert "the dean" in body
    assert "agreed the format" in body


def test_a_skill_note_numbers_its_steps(vault):
    path = vault.skill_note(
        name="Grade report", description="how these are made", steps=["find the file", "total it"]
    )
    _, body = vault.read_note(str(path.relative_to(vault.root)))
    assert "1. find the file" in body
    assert "2. total it" in body


def test_archiving_moves_a_note_rather_than_deleting_it(vault):
    """Archiving is not forgetting. The vault is the owner's notebook, and removing pages
    from it is not Thursday's call — memory deletion is handled where "forget" means gone."""
    path = vault.write_note(folder="00 Inbox", title="Old thought", body="keep me")
    moved = vault.archive(str(path.relative_to(vault.root)))

    assert moved is not None
    assert moved.parent.name == "09 Archive"
    assert not path.exists()
    assert "keep me" in moved.read_text(encoding="utf-8")


def test_archiving_twice_does_not_overwrite_the_first_copy(vault):
    first = vault.write_note(folder="00 Inbox", title="Duplicate", body="one")
    vault.archive(str(first.relative_to(vault.root)))
    second = vault.write_note(folder="00 Inbox", title="Duplicate", body="two")
    vault.archive(str(second.relative_to(vault.root)))

    archived = sorted((vault.root / "09 Archive").glob("*.md"))
    assert len(archived) == 2


def test_the_inbox_takes_something_with_no_home_yet(vault):
    path = vault.inbox("look into the pgvector index later")
    assert path is not None and path.parent.name == "00 Inbox"


@pytest.mark.parametrize(
    "call",
    [
        lambda v: v.update_note("x.md", body="y"),
        lambda v: v.link_notes("a.md", "b.md"),
        lambda v: v.tag_note("a.md", "t"),
        lambda v: v.archive("a.md"),
        lambda v: v.inbox("x"),
        lambda v: v.meeting_note(title="t", attendees=[], notes="n"),
        lambda v: v.skill_note(name="s", description="d", steps=[]),
        lambda v: v.person_note(name="p", notes="n"),
    ],
)
def test_every_new_call_is_a_no_op_when_the_vault_is_disabled(tmp_path, call):
    """§8 — a privacy zone can switch the vault off, and nothing may then write to disk."""
    disabled = ObsidianVault(tmp_path / "vault", enabled=False)
    assert call(disabled) is None
    assert not (tmp_path / "vault").exists()


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
