"""The Teacher agent, and the Supervisor checking its arithmetic (V13).

The last two tests are the point of the whole design. The agent hands up `percentages` and
`count`, `Supervisor._check_arithmetic` recomputes them, and a rubric whose weights did not
total 100 would be caught by something other than the agent that made it.
"""

from __future__ import annotations

import pytest
from thursday_agents.teacher import ACTIONS, TeacherAgent
from thursday_core.supervisor import Supervisor
from thursday_shared.ids import new_id
from thursday_shared.models import AgentResult, JobContract, Spend

CRITERIA = [
    {"name": "เนื้อหา", "weight": 40},
    {"name": "การนำเสนอ", "weight": 35},
    {"name": "สื่อประกอบ", "weight": 25},
]
ROWS = [
    {"topic": "สาร", "items": {"จำ": 3, "เข้าใจ": 2}, "marks_each": 1, "periods": 4},
    {"topic": "แรง", "items": {"วิเคราะห์": 3}, "marks_each": 2, "periods": 6},
]
ACTIVITIES = [
    {"name": "นำเข้าสู่บทเรียน", "minutes": 5, "phase": "ขั้นนำ"},
    {"name": "ทดลอง", "minutes": 30, "phase": "ขั้นสอน"},
    {"name": "สรุป", "minutes": 10, "phase": "ขั้นสรุป"},
]


class Ctx:
    spend = Spend()

    async def emit(self, event):
        return None


def contract(**inputs) -> JobContract:
    return JobContract(
        task_id=new_id(), step_id=new_id(), agent="teacher", objective="งานครู", inputs=inputs
    )


@pytest.fixture
def agent() -> TeacherAgent:
    return TeacherAgent()


async def test_it_builds_a_rubric_with_the_points_worked_out(agent):
    result = await agent.run(
        contract(action="rubric", title="โครงงาน", total_points=20, criteria=CRITERIA), Ctx()
    )
    assert result.ok, result.error
    points = {c["criterion"]: c["points"] for c in result.output["document"]["criteria"]}
    assert points == {"เนื้อหา": 8.0, "การนำเสนอ": 7.0, "สื่อประกอบ": 5.0}
    assert "100%" in result.summary


async def test_it_scores_a_student_against_a_rubric(agent):
    result = await agent.run(
        contract(
            action="score",
            total_points=20,
            criteria=CRITERIA,
            awarded={"เนื้อหา": "ดีมาก", "การนำเสนอ": "ดี", "สื่อประกอบ": "พอใช้"},
        ),
        Ctx(),
    )
    assert result.ok
    assert result.output["document"]["earned"] == 15.75
    assert result.output["document"]["percent"] == 78.75


async def test_it_builds_a_blueprint_and_reports_the_higher_order_share(agent):
    result = await agent.run(
        contract(action="blueprint", title="กลางภาค", rows=ROWS, expect_items=8), Ctx()
    )
    assert result.ok, result.error
    document = result.output["document"]
    assert document["total_items"] == 8
    assert document["total_marks"] == 11.0
    assert document["higher_order_percent"] == pytest.approx(54.55, abs=0.01)


async def test_it_times_a_lesson_against_the_period(agent):
    result = await agent.run(
        contract(action="lesson", title="แรงเสียดทาน", minutes=50, activities=ACTIVITIES), Ctx()
    )
    assert result.ok, result.error
    assert result.output["document"]["planned_minutes"] == 45
    assert result.output["document"]["slack_minutes"] == 5
    assert "45 นาทีจาก 50 นาที" in result.summary


# ------------------------------------------------------------------------------ refusals


async def test_a_rubric_that_does_not_total_a_hundred_is_refused_with_the_numbers(agent):
    result = await agent.run(
        contract(action="rubric", criteria=[{"name": "ก", "weight": 40}]), Ctx()
    )
    assert not result.ok
    assert "40%" in result.error and "100%" in result.error
    assert result.output["document"] == {}


async def test_a_lesson_that_overruns_is_refused_rather_than_trimmed(agent):
    result = await agent.run(
        contract(
            action="lesson",
            title="เกิน",
            minutes=50,
            activities=[{"name": "ก", "minutes": 40}, {"name": "ข", "minutes": 30}],
        ),
        Ctx(),
    )
    assert not result.ok
    assert "70" in result.error and "20 นาที" in result.error


async def test_a_blueprint_contradicting_its_own_total_is_refused(agent):
    result = await agent.run(contract(action="blueprint", rows=ROWS, expect_items=12), Ctx())
    assert not result.ok
    assert "12" in result.error and "8" in result.error


async def test_an_unknown_action_lists_the_ones_that_exist(agent):
    result = await agent.run(contract(action="ทำข้อสอบให้เลย"), Ctx())
    assert not result.ok
    for action in ACTIONS:
        assert action in result.error


async def test_malformed_input_is_refused_rather_than_guessed(agent):
    result = await agent.run(contract(action="rubric", criteria=[{"name": "ก"}]), Ctx())
    assert not result.ok
    assert "weight" in result.error


# ------------------------------------------------------- the Supervisor checks the numbers


async def test_the_supervisor_recomputes_the_weights_for_itself(agent):
    """Not a test of the agent. A test that the agent hands over enough for something else
    to catch it being wrong (§18)."""
    result = await agent.run(contract(action="rubric", total_points=20, criteria=CRITERIA), Ctx())
    report = await Supervisor(models=None, use_llm_critique=False).verify(
        contract(action="rubric"), result
    )
    names = {c["name"] for c in report.checks}
    assert "percentages_total" in names, "the weights were not offered for checking"
    assert "count_matches_items" not in names or all(c["ok"] for c in report.checks)
    assert report.verdict.value == "PASS", report.checks


async def test_a_rubric_whose_weights_were_faked_would_not_survive_the_supervisor():
    """The check has teeth: a result claiming weights that do not total 100 fails, even
    though the agent that made it said it was fine."""
    faked = AgentResult(
        agent="teacher",
        ok=True,
        output={"document": {}, "summary": "", "action": "rubric", "percentages": [40.0, 40.0]},
        summary="รูบริกที่น้ำหนักไม่ครบ",
    )
    report = await Supervisor(models=None, use_llm_critique=False).verify(
        contract(action="rubric"), faked
    )
    failed = [c for c in report.checks if c["name"] == "percentages_total"]
    assert failed and not failed[0]["ok"]
    assert report.verdict.value != "PASS"


async def test_the_blueprint_shares_are_offered_for_checking_too(agent):
    result = await agent.run(contract(action="blueprint", rows=ROWS), Ctx())
    assert sum(result.output["percentages"]) == pytest.approx(100.0, abs=0.05)


# --------------------------------------------------------------------------------- wiring


def test_the_agent_is_registered_and_cannot_write_anything(container):
    """READ ceiling: it computes and returns. Saving a document is `file.write` through the
    ordinary permission path, by whoever asked for it."""
    from thursday_shared.enums import PermissionLevel

    spec = container.agents.get("teacher").spec
    assert spec.permission_ceiling is PermissionLevel.READ
    assert spec.tools == []
    assert spec.privacy_profile == "local_only"
    assert spec.output_schema
    assert spec.user_description and spec.user_examples
