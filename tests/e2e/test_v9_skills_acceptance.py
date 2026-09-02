"""V9 acceptance test — "Thursday ทำรายงานคะแนนแบบที่เคยทำ".

    retrieve the skill → build a plan → run several agents → Supervisor validates → report

The sentence names no file, no steps and no skill. It says the kind of work and that there
was a previous time, and everything else has to come from what Thursday learned. Before V9
it parsed as a research question and came back "I found nothing" while the skill sat in the
registry.

The report at the end is the part worth being strict about. It is the artefact someone acts
on and forwards, so the test does not check that a document was produced — it checks that
the document contains the figures that were computed for it. An earlier version passed
`output.document is not empty` with the model's offline apology.
"""

from __future__ import annotations

import pytest
from thursday_automation.skills.models import SkillStep
from thursday_devices.hub import LoopbackDeviceSession
from thursday_devices.node.executor import NodeExecutor
from thursday_shared.enums import TaskState
from thursday_shared.ids import new_id
from thursday_shared.models import UserRequest

from tests.conftest import FakeAdapter

#: Four active students and one who left. The one who left is the point: a report that
#: quietly counted them would be wrong in a way that looks right.
GRADES = "name,score,active\nAnong,82,yes\nBoon,45,yes\nChai,91,yes\nDao,38,no\n"


@pytest.fixture
async def workshop(container, tmp_path):
    """A machine, a grades file, and the skill the owner taught once before."""
    grades = tmp_path / "grades.csv"
    grades.write_text(GRADES, encoding="utf-8")

    device_id = new_id()
    node = LoopbackDeviceSession(
        device_id=device_id,
        name="Office-PC",
        executor=NodeExecutor(FakeAdapter(), allowed_roots=[tmp_path]),
    )
    await container.hub.register(node, location_context="office")
    container.world.update(active_device_id=device_id, active_device_name="Office-PC")

    skill = container.skills.capture(
        name="School Grade Report",
        description="อ่านไฟล์คะแนน ตัดคนที่ไม่ active ออก คำนวณเกรดกับ % ที่ผ่าน แล้วทำรายงาน",
        steps=[
            SkillStep(seq=0, tool="file.read", args={"path": str(grades)}),
            SkillStep(seq=1, agent="data", args={"pass_mark": 50}),
            SkillStep(seq=2, agent="document", args={}),
        ],
    )
    await container.skills.test(skill.id)
    container.skills.approve(skill.id, approved_by="owner")
    container.skills.activate(skill.id)
    return node, skill, grades


# ------------------------------------------------------------------ the acceptance flow


async def test_the_skill_is_retrieved_planned_and_run(container, session_id, workshop):
    _, skill, _ = workshop
    response = await container.engine.handle_request(
        UserRequest(conversation_id=session_id, text="Thursday ทำรายงานคะแนนแบบที่เคยทำ")
    )

    assert response.task_id is not None
    task = container.tasks.get(response.task_id)
    assert task is not None and task.plan is not None
    # The plan came from the skill, not from the planner's generic analysis template.
    assert task.plan.objective == skill.name
    assert [step.name for step in task.plan.steps] == ["computer", "data", "document"]


async def test_every_step_completes_and_the_supervisor_passes_it(container, session_id, workshop):
    response = await container.engine.handle_request(
        UserRequest(conversation_id=session_id, text="Thursday ทำรายงานคะแนนแบบที่เคยทำ")
    )
    task = container.tasks.get(response.task_id)

    assert task.status is TaskState.COMPLETED
    assert all(step.status is TaskState.COMPLETED for step in task.plan.steps)
    assert task.verification is not None and task.verification.passed
    assert response.verified is True


async def test_the_analysis_excludes_the_inactive_record(container, session_id, workshop):
    """ "ตัดคนที่ไม่ active ออก" is a step the owner stated. A report that quietly included
    Dao would be wrong in a way that reads as correct."""
    response = await container.engine.handle_request(
        UserRequest(conversation_id=session_id, text="Thursday ทำรายงานคะแนนแบบที่เคยทำ")
    )
    task = container.tasks.get(response.task_id)
    analysis = next(s.output for s in task.plan.steps if s.name == "data")

    assert analysis["count"] == 3
    assert analysis["excluded"] == 1
    assert [row["name"] for row in analysis["rows"]] == ["Anong", "Boon", "Chai"]


async def test_the_figures_are_computed_not_generated(container, session_id, workshop):
    """A model asked for an average returns a plausible number, and a plausible wrong number
    survives every check except the one nobody ran. 82, 45 and 91 have a mean of 72.6667."""
    response = await container.engine.handle_request(
        UserRequest(conversation_id=session_id, text="Thursday ทำรายงานคะแนนแบบที่เคยทำ")
    )
    task = container.tasks.get(response.task_id)
    analysis = next(s.output for s in task.plan.steps if s.name == "data")

    assert analysis["metrics"]["score"]["mean"] == pytest.approx(72.6667, abs=1e-4)
    assert analysis["metrics"]["score"]["max"] == 91.0
    # Two of three at or above 50, reported as percentages that sum to 100 so the
    # Supervisor's arithmetic check can confirm them.
    assert analysis["passed"] == 2
    assert sum(analysis["percentages"]) == pytest.approx(100.0)


async def test_the_report_contains_the_figures_it_reports_on(container, session_id, workshop):
    """Not "a document was produced". `output.document is not empty` was satisfied, once, by
    the model's offline apology — a document shaped like a report with none of the analysis
    in it, and every check saying PASS."""
    response = await container.engine.handle_request(
        UserRequest(conversation_id=session_id, text="Thursday ทำรายงานคะแนนแบบที่เคยทำ")
    )
    task = container.tasks.get(response.task_id)
    report = next(s.output for s in task.plan.steps if s.name == "document")

    assert report["grounded"] is True
    assert "3" in report["document"]  # the analysed count
    assert "72.6667" in report["document"] or "72.67" in report["document"]
    assert report["sources"] == ["data"]


async def test_the_report_is_titled_by_the_job_not_the_step(container, session_id, workshop):
    """A step objective names one slot in a job. "document: as demonstrated" at the top of
    something someone forwards is worse than useless."""
    response = await container.engine.handle_request(
        UserRequest(conversation_id=session_id, text="Thursday ทำรายงานคะแนนแบบที่เคยทำ")
    )
    task = container.tasks.get(response.task_id)
    report = next(s.output for s in task.plan.steps if s.name == "document")

    assert report["document"].startswith("# School Grade Report")


# ------------------------------------------------------------------ what must not happen


async def test_a_skill_that_has_not_been_approved_does_not_run(container, session_id, tmp_path):
    """The lifecycle is the only thing standing between "Thursday watched me once" and
    "Thursday does this to real files unattended"."""
    grades = tmp_path / "grades.csv"
    grades.write_text(GRADES, encoding="utf-8")
    container.skills.capture(
        name="School Grade Report",
        description="อ่านไฟล์คะแนน ตัดคนที่ไม่ active ออก แล้วทำรายงาน",
        steps=[SkillStep(seq=0, tool="file.read", args={"path": str(grades)})],
    )  # captured, never tested or approved

    response = await container.engine.handle_request(
        UserRequest(conversation_id=session_id, text="Thursday ทำรายงานคะแนนแบบที่เคยทำ")
    )
    assert "draft" in response.text or "not active" in response.text
    assert container.tasks.list() == []


async def test_no_skill_falls_through_to_ordinary_planning(container, session_id, office_pc):
    """ "แบบเดิม" with nothing learned is not a missing skill — it is the owner pointing at a
    remembered instruction (§7). Announcing a missing skill would answer a question they did
    not ask while ignoring the one they did."""
    await container.engine.handle_request(
        UserRequest(conversation_id=session_id, text="Thursday เปิด notepad แบบเดิม")
    )
    assert "notepad" in office_pc.executor.adapter.running


async def test_two_skills_that_match_equally_produce_a_question(container, session_id, tmp_path):
    grades = tmp_path / "grades.csv"
    grades.write_text(GRADES, encoding="utf-8")
    for name in ("Grade Report A", "Grade Report B"):
        skill = container.skills.capture(
            name=name,
            description="อ่านไฟล์คะแนน ตัดคนที่ไม่ active ออก แล้วทำรายงาน",
            steps=[SkillStep(seq=0, tool="file.read", args={"path": str(grades)})],
        )
        await container.skills.test(skill.id)
        container.skills.approve(skill.id, approved_by="owner")
        container.skills.activate(skill.id)

    response = await container.engine.handle_request(
        UserRequest(conversation_id=session_id, text="Thursday ทำรายงานคะแนนแบบที่เคยทำ")
    )
    assert "Grade Report A" in response.text and "Grade Report B" in response.text
    assert container.tasks.list() == []
