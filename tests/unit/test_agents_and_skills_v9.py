"""Data and document agents, skill matching, and the skill→plan bridge (§15, §50–53, V9)."""

from __future__ import annotations

import pytest
from thursday_agents.data import describe, is_active, numeric_columns, parse_rows
from thursday_agents.grounding import grounded, numbers_in
from thursday_automation.skills.matching import (
    MATCH_FLOOR,
    find_skill,
    mentions_a_previous_time,
    score_skill,
)
from thursday_automation.skills.models import Skill, SkillStatus, SkillStep, SkillVersion
from thursday_automation.skills.planning import (
    SkillNotRunnable,
    agent_for,
    compose,
    plan_from_skill,
)
from thursday_core.intent_rules import parse, without_like_before

CSV = "name,score,active\nAnong,82,yes\nBoon,45,yes\nChai,91,yes\nDao,38,no\n"


def make_skill(
    name: str,
    *,
    description: str = "",
    steps: list[SkillStep] | None = None,
    status: SkillStatus = SkillStatus.ACTIVE,
    tags: list[str] | None = None,
) -> Skill:
    skill = Skill(name=name, slug=name.lower().replace(" ", "-"), description=description)
    skill.status = status
    skill.tags = tags or []
    version = SkillVersion(
        steps=steps or [SkillStep(seq=0, tool="file.read", args={"path": "/tmp/x.csv"})]
    )
    skill.add_version(version)
    skill.current_version = version.version
    return skill


# ------------------------------------------------------------------ parsing tabular data


@pytest.mark.parametrize(
    "payload",
    [
        CSV,
        {"content": CSV},
        {"rows": [{"name": "Anong", "score": "82"}]},
        [{"name": "Anong", "score": "82"}],
    ],
)
def test_rows_are_recovered_from_whatever_the_previous_step_produced(payload):
    assert parse_rows(payload)


@pytest.mark.parametrize("payload", ["", "   ", None, 42, "just a sentence with no commas"])
def test_unparseable_input_yields_no_rows_rather_than_one_bad_row(payload):
    """An agent that invents structure to have something to analyse produces confident
    nonsense — a "table" of one row holding a blob of prose, and statistics about it."""
    assert parse_rows(payload) == []


def test_inactive_records_are_excluded():
    assert is_active({"name": "A", "active": "yes"})
    assert not is_active({"name": "D", "active": "no"})
    assert not is_active({"name": "E", "status": "ลาออก"})


def test_a_record_with_no_status_column_is_counted():
    """Unknown means counted. Silently dropping records the agent did not understand would
    shrink a total with no trace, and a total quietly too small reads exactly like a right
    one."""
    assert is_active({"name": "A", "score": "70"})
    assert is_active({"name": "B", "active": "something nobody recognises"})


def test_numeric_columns_survive_a_blank_cell():
    rows = [{"score": "80"}, {"score": ""}, {"score": "90"}, {"score": "N/A"}]
    assert numeric_columns(rows) == {"score": [80.0, 90.0]}


def test_a_column_of_mostly_text_is_not_treated_as_numeric():
    rows = [{"note": "x"}, {"note": "y"}, {"note": "3"}, {"note": "z"}]
    assert "note" not in numeric_columns(rows)


def test_statistics_are_computed_not_asked_for():
    stats = describe([10.0, 20.0, 30.0])
    assert stats["mean"] == 20.0
    assert stats["median"] == 20.0
    assert stats["min"] == 10.0 and stats["max"] == 30.0


# ------------------------------------------------------------------ grounding


def test_a_report_that_contains_none_of_its_figures_is_not_grounded():
    """The failure that motivated this: running offline, the model returned "I cannot answer
    analytical questions right now". Non-empty, so it passed `document is not empty`, and
    the owner got a document shaped like a report containing none of the analysis."""
    apology = "ตอนนี้ผมทำงานในโหมดออฟไลน์ จึงตอบคำถามเชิงวิเคราะห์ไม่ได้"
    assert not grounded(apology, {"count": 4, "mean": 71.25})


def test_a_report_carrying_one_of_its_figures_is_grounded():
    assert grounded("4 students were analysed.", {"count": 4})
    assert grounded("The mean was 71.25.", {"metrics": {"score": {"mean": 71.25}}})


def test_thousands_separators_do_not_hide_a_figure():
    assert grounded("Total was 1,250 rows.", {"count": 1250})


def test_with_no_figures_any_text_is_grounded():
    """Nothing was computed, so nothing could have been dropped."""
    assert grounded("A summary.", {})
    assert not grounded("   ", {})


def test_booleans_are_not_numbers():
    """`True` is an `int` in Python. Without this, a report containing the digit 1 would
    count as grounded in a flag nobody wrote down."""
    assert numbers_in({"verified": True, "count": 3}) == [3]


# ------------------------------------------------------------------ skill matching


def test_a_thai_request_finds_an_english_named_skill():
    """The case the first implementation got silently wrong. A skill called "School Grade
    Report" is asked for in Thai; its Thai description is the only thing they share."""
    skill = make_skill(
        "School Grade Report",
        description="อ่านไฟล์คะแนน ตัดคนที่ไม่ active ออก คำนวณเกรดกับ % ที่ผ่าน แล้วทำรายงาน",
    )
    match = find_skill("ทำรายงานคะแนนแบบที่เคยทำ", [skill])
    assert match is not None and match.confident
    assert match.skill is skill


def test_a_skill_named_in_the_sentence_matches_outright():
    skill = make_skill("Grade Report", description="anything")
    assert score_skill("run the grade report like last time", skill) > MATCH_FLOOR


def test_an_unrelated_skill_does_not_match():
    skill = make_skill("Backup Photos", description="copy photos from the phone to the NAS")
    assert find_skill("ทำรายงานคะแนนแบบที่เคยทำ", [skill]) is None


def test_two_equally_good_matches_produce_a_question_not_a_pick():
    """Running the wrong workflow is worse than asking: its steps have already happened by
    the time anyone notices."""
    a = make_skill("Grade Report A", description="อ่านไฟล์คะแนน แล้วทำรายงาน")
    b = make_skill("Grade Report B", description="อ่านไฟล์คะแนน แล้วทำรายงาน")
    match = find_skill("ทำรายงานคะแนนแบบที่เคยทำ", [a, b])
    assert match is not None
    assert not match.confident
    assert "Grade Report" in match.question()


def test_no_skills_at_all_is_no_match():
    assert find_skill("anything", []) is None


@pytest.mark.parametrize(
    "said", ["ทำรายงานแบบเดิม", "do it like last time", "make the report as usual"]
)
def test_a_previous_time_is_recognised(said):
    assert mentions_a_previous_time(said)


def test_the_usual_place_is_a_location_not_a_skill_marker():
    """ "in the usual place" names a folder. Treating it as a skill request hijacks the
    sentence — which it did, until a file-search test caught it."""
    assert parse("Thursday find excel in the usual place") is None


def test_stripping_the_marker_does_not_eat_thai_letters():
    """`str.strip("ครับค่ะ")` removes *characters*, so a word ending in ร or บ loses its
    last letter. The marker is removed as a suffix instead."""
    assert without_like_before("ทำรายงานคะแนนแบบที่เคยทำ") == "ทำรายงานคะแนน"


# ------------------------------------------------------------------ skill → plan


def test_only_an_active_skill_becomes_a_plan():
    """Draft and testing exist so a learned workflow can be examined before it touches real
    data. A converter that quietly ran a draft would remove the only thing they are for."""
    draft = make_skill("Draft Thing", status=SkillStatus.DRAFT)
    with pytest.raises(SkillNotRunnable, match="not active"):
        plan_from_skill(draft)


def test_a_tool_step_becomes_a_device_action_and_an_agent_step_does_not():
    skill = make_skill(
        "Mixed",
        steps=[
            SkillStep(seq=0, tool="file.read", args={"path": "/tmp/x.csv"}),
            SkillStep(seq=1, agent="data", args={"pass_mark": 50}),
        ],
    )
    plan = plan_from_skill(skill)
    assert plan.steps[0].name == "computer"
    assert plan.steps[0].args["action"] == "file.read"
    assert plan.steps[1].name == "data"
    assert "action" not in plan.steps[1].args


def test_steps_run_in_the_order_they_were_demonstrated():
    """A demonstration is a sequence, and nothing in it says which adjacent steps were
    independent. Guessing that they were, to run them in parallel, reorders someone's
    workflow on no evidence."""
    skill = make_skill(
        "Chain",
        steps=[
            SkillStep(seq=0, tool="file.read", args={}),
            SkillStep(seq=1, agent="data", args={}),
            SkillStep(seq=2, agent="document", args={}),
        ],
    )
    plan = plan_from_skill(skill)
    assert plan.steps[1].depends_on == [plan.steps[0].id]
    assert plan.steps[2].depends_on == [plan.steps[1].id]


def test_supplied_inputs_cannot_overwrite_what_the_skill_does():
    """A caller that could rewrite a step's arguments could turn "read this file" into
    "delete that one" while still calling it by the skill's trusted name."""
    skill = make_skill(
        "Fixed", steps=[SkillStep(seq=0, tool="file.read", args={"path": "/safe/path.csv"})]
    )
    plan = plan_from_skill(skill, inputs={"path": "/etc/shadow", "pass_mark": 50})
    assert plan.steps[0].args["args"]["path"] == "/safe/path.csv"
    assert plan.steps[0].args["args"]["pass_mark"] == 50  # what the skill left open is filled


@pytest.mark.parametrize(
    ("tool", "expected"),
    [
        ("file.folder.create", "computer"),
        ("app.open", "computer"),
        ("browser.open", "browser"),
        ("web.search", "research"),
        ("obsidian.search", "research"),
    ],
)
def test_tools_map_to_agents_by_namespace(tool, expected):
    assert agent_for(tool) == expected


def test_a_step_names_exactly_one_of_tool_or_agent():
    """Inferring the kind from the name was tried: an unregistered tool and an agent job
    look identical, so a typo validates as an agent step."""
    with pytest.raises(ValueError, match="exactly one"):
        SkillStep(seq=0, tool="file.read", agent="data")
    with pytest.raises(ValueError, match="exactly one"):
        SkillStep(seq=0)


# ------------------------------------------------------------------ composition


def test_composing_chains_every_step_of_every_part():
    a = make_skill("Find", steps=[SkillStep(seq=0, tool="file.search", args={})])
    b = make_skill("Analyse", steps=[SkillStep(seq=0, agent="data", args={})])
    steps, sources = compose("Grade Report", "find then analyse", [a, b])
    assert [s.tool or s.agent for s in steps] == ["file.search", "data"]
    assert sources == ["Find", "Analyse"]


def test_composing_records_where_each_step_came_from():
    """Months later, "which part of this came from where" is the first thing anyone
    editing it needs to know."""
    a = make_skill("Find", steps=[SkillStep(seq=0, tool="file.search", args={})])
    b = make_skill("Analyse", steps=[SkillStep(seq=0, agent="data", args={})])
    steps, _ = compose("Grade Report", "", [a, b])
    assert steps[0].condition == "from Find"


def test_a_draft_cannot_be_composed_in():
    """Chaining a draft into a composition would give it a way to run that the lifecycle
    exists to deny it."""
    active = make_skill("Find", steps=[SkillStep(seq=0, tool="file.search", args={})])
    draft = make_skill("Half Done", status=SkillStatus.DRAFT)
    with pytest.raises(SkillNotRunnable, match="not"):
        compose("Mixed", "", [active, draft])


def test_composing_one_skill_is_not_a_composition():
    only = make_skill("Find", steps=[SkillStep(seq=0, tool="file.search", args={})])
    with pytest.raises(SkillNotRunnable, match="at least two"):
        compose("Pointless", "", [only])


def test_a_composed_skill_starts_as_a_draft(container):
    """Three workflows approved separately are not the same thing as one workflow that does
    all three in sequence, and the second is what the owner would be agreeing to."""
    a = container.skills.capture(
        name="Find", description="find", steps=[SkillStep(seq=0, tool="file.search", args={})]
    )
    b = container.skills.capture(
        name="Analyse", description="analyse", steps=[SkillStep(seq=0, agent="data", args={})]
    )
    for skill in (a, b):
        skill.status = SkillStatus.ACTIVE

    composed = container.skills.compose(
        name="Grade Report", description="find then analyse", skill_ids=[a.id, b.id]
    )
    assert composed.status is SkillStatus.DRAFT
    assert "composed" in composed.tags
    assert composed.latest is not None
    assert composed.latest.changelog == "composed from Find, Analyse"


# ------------------------------------------------------------------ sandbox


async def test_a_skill_delegating_to_an_unknown_agent_fails_review(container):
    """Caught in the sandbox rather than halfway through a run against real data."""
    skill = container.skills.capture(
        name="Broken",
        description="",
        steps=[SkillStep(seq=0, agent="nonexistent", args={})],
    )
    result = await container.skills.test(skill.id)
    assert not result.ok
    assert any("unknown agent" in failure for failure in result.failures)
