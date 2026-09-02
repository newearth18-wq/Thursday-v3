"""V5 acceptance test — the second brain.

    Turn 1: "Thursday จำไว้ว่ารายงานแบบนี้ให้สรุปเป็นตารางก่อน"
    Turn 2: "Thursday ทำรายงานแบบเดิม"

The second turn must *use* what the first one stored. That is the whole distinction V5
turns on: a system that can recall an instruction but never acts on it is a notebook, and
the owner who took the trouble to say it would have to say it again every time.

The negative cases matter as much. An instruction the owner retracted must stop being
applied, a low-confidence guess must not be applied at all, and a credential must never
have been stored in the first place — whatever they said.
"""

from __future__ import annotations

import pytest
from thursday_shared.enums import MemoryLayer, MemorySource
from thursday_shared.ids import new_id
from thursday_shared.models import MemoryQuery, MemoryWrite, UserRequest


async def test_a_remembered_instruction_shapes_the_next_report(container, session_id):
    # Turn 1 — the owner says how these reports should be written.
    first = await container.engine.handle_request(
        UserRequest(
            conversation_id=session_id,
            text="Thursday จำไว้ว่ารายงานแบบนี้ให้สรุปเป็นตารางก่อน",
        )
    )
    assert "จำไว้แล้ว" in first.text or "Noted" in first.text

    # It went to the procedural layer. Filed as semantic it would be recallable trivia,
    # never applied to anything.
    stored = await container.memory.recall(MemoryQuery(text="รายงาน สรุป ตาราง", k=10))
    procedures = [r for r in stored if r.layer is MemoryLayer.PROCEDURAL]
    assert procedures, [str(r.layer) for r in stored]
    assert "ตาราง" in procedures[0].content

    # Turn 2 — a later, differently worded request for the same kind of work.
    await container.engine.handle_request(
        UserRequest(conversation_id=new_id(), text="Thursday ทำรายงานคะแนนแบบเดิม")
    )

    task = container.tasks.list()[0]
    assert task.plan is not None

    # The plan records what it is following, so the owner can see *why* the output looks
    # the way it does and correct the memory rather than the output.
    assert task.plan.following, "the remembered instruction was not applied to the plan"
    assert any("ตาราง" in instruction for instruction in task.plan.following)

    # And it reached the step that produces the document, not the one that finds the file.
    producing = [s for s in task.plan.steps if s.name in {"document", "data"}]
    assert producing
    assert any("conventions" in step.args for step in producing)


async def test_the_instruction_is_not_pasted_onto_unrelated_steps(container, session_id):
    """Telling a file search to start with a summary table is noise, and noise in an
    objective is what makes an agent do the wrong thing."""
    await container.engine.handle_request(
        UserRequest(
            conversation_id=session_id,
            text="Thursday จำไว้ว่ารายงานแบบนี้ให้สรุปเป็นตารางก่อน",
        )
    )
    await container.engine.handle_request(
        UserRequest(conversation_id=new_id(), text="Thursday ทำรายงานคะแนนแบบเดิม")
    )

    task = container.tasks.list()[0]
    gather = [s for s in task.plan.steps if s.name == "computer"]
    assert gather
    assert all("conventions" not in step.args for step in gather)


async def test_a_retracted_instruction_stops_being_applied(container, session_id):
    """ "ลืมเรื่อง X" has to reach the behaviour, not just the store."""
    await container.engine.handle_request(
        UserRequest(
            conversation_id=session_id,
            text="Thursday จำไว้ว่ารายงานแบบนี้ให้สรุปเป็นตารางก่อน",
        )
    )
    forgotten = await container.engine.handle_request(
        UserRequest(conversation_id=session_id, text="Thursday ลืมเรื่องรายงานสรุปตาราง")
    )
    assert "ลืมแล้ว" in forgotten.text

    await container.engine.handle_request(
        UserRequest(conversation_id=new_id(), text="Thursday ทำรายงานคะแนนแบบเดิม")
    )
    task = container.tasks.list()[0]
    assert not any("ตาราง" in instruction for instruction in (task.plan.following or []))


async def test_a_low_confidence_guess_is_not_applied(container):
    """Acting on a guess about how the owner wants their work done is worse than asking."""
    await container.memory.write(
        MemoryWrite(
            layer=MemoryLayer.PROCEDURAL,
            content="maybe they want charts in every report",
            source=MemorySource.AGENT,
            confidence=0.4,
            importance=0.7,
        ),
        force=True,
    )
    await container.engine.handle_request(
        UserRequest(conversation_id=new_id(), text="Thursday ทำรายงานคะแนน")
    )
    task = container.tasks.list()[0]
    assert not any("charts" in i for i in (task.plan.following or []))


async def test_a_credential_is_never_stored_however_it_is_phrased(container, session_id):
    """§35 — "remember" is the owner's strongest signal and still does not override this."""
    response = await container.engine.handle_request(
        UserRequest(
            conversation_id=session_id,
            text="Thursday remember these reports should use api key sk-live-9f2a7c1e4b8d0000",
        )
    )
    assert "sk-live" not in response.text
    assert not await container.memory.recall(MemoryQuery(text="api key", k=5))


async def test_dont_remember_this_removes_what_this_conversation_wrote(container, session_id):
    """Stopping future writes alone would leave the thing the owner pointed at in memory,
    which is the opposite of what they asked for."""
    await container.engine.handle_request(
        UserRequest(conversation_id=session_id, text="Thursday จำไว้ว่าห้องประชุมคือ 402")
    )
    assert await container.memory.recall(MemoryQuery(text="ห้องประชุม", k=5))

    ack = await container.engine.handle_request(
        UserRequest(conversation_id=session_id, text="Thursday อย่าจำเรื่องนี้")
    )
    assert "ไม่เก็บ" in ack.text or "won't keep" in ack.text

    # Gone.
    assert not await container.memory.recall(MemoryQuery(text="ห้องประชุม", k=5))

    # And nothing this conversation does *incidentally* is written from here on.
    before = len(await container.memory.recall(MemoryQuery(text="", k=100)))
    await container.engine.handle_request(
        UserRequest(conversation_id=session_id, text="Thursday สถานะงานเป็นยังไง")
    )
    assert len(await container.memory.recall(MemoryQuery(text="", k=100))) == before


async def test_suppression_does_not_gag_a_later_explicit_instruction(container, session_id):
    """ "Don't remember this" is about what was just said, not a setting.

    An owner who then says "จำไว้ว่า X" plainly wants X kept, and treating the earlier
    sentence as a standing gag would mean silently ignoring the clearest instruction there
    is — which is the same failure as remembering what they asked to forget, in reverse.
    """
    await container.engine.handle_request(
        UserRequest(conversation_id=session_id, text="Thursday อย่าจำเรื่องนี้")
    )
    await container.engine.handle_request(
        UserRequest(conversation_id=session_id, text="Thursday จำไว้ว่าห้องทำงานผมคือ 402")
    )
    assert await container.memory.recall(MemoryQuery(text="ห้องทำงาน", k=5))


async def test_forgetting_names_what_it_removed(container, session_id):
    """ "Forgotten" alone leaves the owner unable to tell a deletion from a search that
    matched nothing, and those need different follow-ups."""
    empty = await container.engine.handle_request(
        UserRequest(conversation_id=session_id, text="Thursday ลืมเรื่องเรือดำน้ำ")
    )
    assert "ไม่ได้เก็บ" in empty.text


@pytest.mark.parametrize("phrase", ["Thursday forget it", "Thursday ลืมมัน"])
async def test_forget_it_is_a_figure_of_speech(container, session_id, phrase):
    """Deleting on that reading is not a mistake that can be undone."""
    await container.memory.write(
        MemoryWrite(layer=MemoryLayer.SEMANTIC, content="something worth keeping"), force=True
    )
    await container.engine.handle_request(UserRequest(conversation_id=session_id, text=phrase))
    assert await container.memory.recall(MemoryQuery(text="worth keeping", k=5))


# ------------------------------------------------------------------ Project Brain (§44)


async def test_the_project_brain_answers_where_things_stand(container):
    """ "ตอนนี้โปรเจกต์นี้ไปถึงไหนแล้ว" — answered from the task table, not from a status
    field somebody forgot to update. A blocked task *is* the blockage."""
    from thursday_core.projects import Decision
    from thursday_shared.enums import TaskState

    project = container.projects.create(name="Grade Report", goal="ส่งรายงานคะแนนภาคเรียนนี้")
    await container.tasks.create(title="รวบรวมคะแนน", objective="collect", project_id=project.id)
    waiting = await container.tasks.create(
        title="รอยืนยันจากอาจารย์", objective="confirm", project_id=project.id
    )
    await container.tasks.transition(waiting.id, TaskState.PLANNING)
    await container.tasks.transition(waiting.id, TaskState.BLOCKED)
    container.projects.record_decision(
        project.id, Decision(decision="ใช้ตารางสรุปก่อนกราฟ", reason="อ่านง่ายกว่า")
    )

    brain = await container.projects.brain(project.id)

    assert brain["goal"] == "ส่งรายงานคะแนนภาคเรียนนี้"
    assert "รอยืนยันจากอาจารย์" in brain["current_state"]
    assert brain["blocked_on"]
    assert brain["recent_decisions"][0]["decision"] == "ใช้ตารางสรุปก่อนกราฟ"

    # Every field PART 44 asks for, present rather than assumed.
    for field in (
        "goal",
        "summary",
        "open_tasks",
        "people",
        "important_files",
        "recent_decisions",
        "timeline",
        "relevant_memories",
        "skills",
    ):
        assert field in brain, field


async def test_the_brain_lists_only_skills_thursday_can_actually_run(container):
    """A draft skill is something Thursday might learn to do, not something it can do.
    Listing the two together would misrepresent what is available."""
    project = container.projects.create(name="Anything", goal="x")
    brain = await container.projects.brain(project.id)
    assert brain["skills"] == []


# ------------------------------------------------------------------ the vault mirror (§8)


async def test_durable_knowledge_reaches_the_owner_s_own_notebook(container):
    """Postgres is where Thursday remembers; Obsidian is where the *owner* does — plain
    Markdown they can read, edit and take with them if Thursday is ever switched off."""
    await container.memory.write(
        MemoryWrite(
            layer=MemoryLayer.PROCEDURAL,
            content="reports open with a summary table, then the chart",
            source=MemorySource.USER,
            importance=0.8,
        ),
        force=True,
    )

    notes = list((container.obsidian.root / "03 Knowledge").glob("*.md"))
    assert notes, "a durable memory left no trace in the vault"
    text = notes[0].read_text(encoding="utf-8")
    assert "summary table" in text
    assert "layer: procedural" in text


async def test_passing_detail_does_not_flood_the_vault(container):
    """A vault with a note for every episodic trace is a vault nobody opens."""
    for index in range(5):
        await container.memory.write(
            MemoryWrite(
                layer=MemoryLayer.EPISODIC,
                content=f"opened chrome, attempt {index}",
                source=MemorySource.AGENT,
                importance=0.55,
            ),
            force=True,
        )
    assert list((container.obsidian.root / "03 Knowledge").glob("*.md")) == []
    assert container.vault_mirror.skipped >= 5


async def test_a_secret_never_reaches_the_vault_through_the_mirror(container):
    """Two defences, and the first one holds.

    The memory manager redacts on the write path, so the record is already clean by the
    time the mirror sees it — the vault's own refusal is the second line, not the first.
    What matters is the outcome: the token is in neither store, and the memory write itself
    still succeeded rather than failing because of a downstream subscriber.
    """
    token = "ghp_0123456789abcdefghijklmnopqrstuvwxyzAB"
    record = await container.memory.write(
        MemoryWrite(
            layer=MemoryLayer.SEMANTIC,
            content=f"the deploy token is {token}",
            source=MemorySource.USER,
            importance=0.9,
        ),
        force=True,
    )

    assert record is not None, "a subscriber's problem must not fail the write"
    assert token not in record.content

    vault_text = "".join(
        path.read_text(encoding="utf-8") for path in (container.obsidian.root).rglob("*.md")
    )
    assert token not in vault_text
