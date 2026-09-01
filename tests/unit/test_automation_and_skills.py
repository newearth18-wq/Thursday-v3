"""Automation, proactivity, routine learning, skills and dynamic agents (§16, §46–53)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from thursday_agents.factory import MAX_DEPTH, MAX_PER_TASK, AgentFactory, DynamicAgentSpec
from thursday_agents.registry import AgentRegistry
from thursday_automation.engine import AutomationEngine, ProactivityGate
from thursday_automation.routines import RoutineLearner
from thursday_automation.rules import Action, Automation, Condition, Trigger
from thursday_automation.skills.models import SkillStatus, SkillStep, SkillTest
from thursday_automation.skills.registry import SkillRegistry
from thursday_core.bus import InProcessEventBus
from thursday_shared.enums import (
    NotificationPriority,
    PermissionLevel,
    ProactivityLevel,
)
from thursday_shared.errors import PermissionDenied, ThursdayError
from thursday_shared.ids import new_id
from thursday_shared.models import Event, PermissionSet

# ------------------------------------------------------------------ automation rules


def test_a_rule_fires_only_when_its_conditions_hold():
    automation = Automation(
        name="notify on failure",
        trigger=Trigger(kind="event", event_kind="task.*"),
        conditions=[Condition(field="event.status", op="eq", value="FAILED")],
        enabled=True,
    )
    failed = Event(kind="task.failed", payload={"status": "FAILED"})
    completed = Event(kind="task.completed", payload={"status": "COMPLETED"})

    assert automation.should_fire(failed, world={}, proactivity=ProactivityLevel.NORMAL)
    assert not automation.should_fire(completed, world={}, proactivity=ProactivityLevel.NORMAL)


def test_a_disabled_rule_never_fires():
    automation = Automation(trigger=Trigger(event_kind="*"), enabled=False)
    assert not automation.should_fire(
        Event(kind="anything"), world={}, proactivity=ProactivityLevel.HIGH
    )


def test_a_rule_below_the_proactivity_floor_stays_quiet():
    automation = Automation(
        trigger=Trigger(event_kind="*"), enabled=True, proactivity_min=ProactivityLevel.HIGH
    )
    assert not automation.should_fire(
        Event(kind="x"), world={}, proactivity=ProactivityLevel.NORMAL
    )
    assert automation.should_fire(Event(kind="x"), world={}, proactivity=ProactivityLevel.HIGH)


async def test_a_suggested_automation_is_never_enabled_on_thursdays_own_say_so():
    """§49 — Thursday proposes; the owner enables."""
    engine = AutomationEngine(bus=InProcessEventBus())
    added = engine.add(
        Automation(name="morning routine", enabled=True, created_by="thursday_suggested")
    )
    assert added.enabled is False

    engine.enable(added.id)
    assert engine.list(enabled_only=True) == [added]


async def test_an_automation_publishes_its_lifecycle():
    bus = InProcessEventBus()
    engine = AutomationEngine(bus=bus)
    automation = engine.add(
        Automation(
            name="weekly summary",
            trigger=Trigger(kind="event", event_kind="event.scheduled"),
            actions=[Action(kind="notify", args={"title": "สรุปงานประจำสัปดาห์"})],
            enabled=True,
        )
    )
    engine.attach()
    await bus.publish(Event(kind="event.scheduled"))
    kinds = [e.kind for e in bus.history()]
    assert "automation.triggered" in kinds
    assert "notification.raised" in kinds
    assert automation.run_count == 1


# ------------------------------------------------------------------ proactivity gate


def test_proactivity_off_silences_everything():
    gate = ProactivityGate(ProactivityLevel.OFF)
    allowed, reason = gate.allows(NotificationPriority.CRITICAL)
    assert not allowed and "off" in reason


def test_low_proactivity_admits_only_critical():
    gate = ProactivityGate(ProactivityLevel.LOW)
    assert gate.allows(NotificationPriority.CRITICAL)[0]
    assert not gate.allows(NotificationPriority.IMPORTANT)[0]


def test_do_not_disturb_yields_only_to_critical():
    gate = ProactivityGate(ProactivityLevel.HIGH)
    assert not gate.allows(NotificationPriority.IMPORTANT, owner_status="dnd")[0]
    assert gate.allows(NotificationPriority.CRITICAL, owner_status="dnd")[0]


def test_private_content_is_not_announced_with_company_present():
    """§67 — the assistant does not read private notifications aloud to a room."""
    gate = ProactivityGate(ProactivityLevel.HIGH)
    allowed, reason = gate.allows(NotificationPriority.NORMAL, private=True, people_present=2)
    assert not allowed and "another person" in reason
    assert gate.allows(NotificationPriority.NORMAL, private=True, people_present=1)[0]


def test_the_hourly_rate_limit_stops_nagging():
    gate = ProactivityGate(ProactivityLevel.HIGH)
    for _ in range(3):
        assert gate.allows(NotificationPriority.NORMAL)[0]
        gate.record(NotificationPriority.NORMAL)
    allowed, reason = gate.allows(NotificationPriority.NORMAL)
    assert not allowed and "limit" in reason
    # A higher priority still gets through.
    assert gate.allows(NotificationPriority.CRITICAL)[0]


# ------------------------------------------------------------------ routine learning


async def test_a_repeated_morning_sequence_becomes_a_proposal_not_an_automation():
    learner = RoutineLearner()
    base = datetime(2026, 3, 2, 8, 15, tzinfo=UTC)
    for day in range(4):
        for tool in ("open_app", "open_url", "list_dir"):
            await learner.on_tool(
                Event(
                    kind="tool.executed",
                    payload={"tool": tool},
                    occurred_at=base + timedelta(days=day),
                )
            )

    candidates = learner.candidates()
    assert candidates, "a four-day repeated sequence should be noticed"
    candidate = candidates[0]
    assert candidate.distinct_days == 4
    assert "Routine" in candidate.describe("th") or "routine" in candidate.describe("en")

    proposal = candidate.to_automation()
    assert proposal.enabled is False
    assert proposal.created_by == "thursday_suggested"


async def test_an_occasional_sequence_is_not_proposed():
    learner = RoutineLearner()
    await learner.on_tool(Event(kind="tool.executed", payload={"tool": "open_app"}))
    assert learner.candidates() == []


# ------------------------------------------------------------------ skills


@pytest.fixture
def skills() -> SkillRegistry:
    return SkillRegistry()


def test_a_captured_skill_starts_as_a_draft(skills):
    skill = skills.capture(
        name="ตรวจข้อสอบ",
        description="อ่านไฟล์คะแนนแล้วสรุป",
        steps=[SkillStep(seq=0, tool="read_file", args={"path": "grades.xlsx"})],
    )
    assert skill.status is SkillStatus.DRAFT
    assert skill.current_version == 1
    assert skills.find("ตรวจข้อสอบ") is skill


async def test_a_draft_cannot_be_activated_before_its_tests_pass(skills):
    skill = skills.capture(name="s", description="d", steps=[SkillStep(seq=0, tool="clock")])
    with pytest.raises(ThursdayError, match="sandbox"):
        skills.activate(skill.id)

    await skills.test(skill.id)
    skills.activate(skill.id)
    assert skills.get(skill.id).status is SkillStatus.ACTIVE


async def test_a_destructive_skill_needs_a_human_approval(skills):
    """§96 — a learned workflow may not delete things on Thursday's own authority."""
    skill = skills.capture(
        name="tidy downloads",
        description="clear the downloads folder",
        steps=[SkillStep(seq=0, tool="delete", args={"path": "~/Downloads/*"})],
        permissions=PermissionSet(max_level=PermissionLevel.MODIFY),
    )
    await skills.test(skill.id)
    with pytest.raises(PermissionDenied, match="approval"):
        skills.activate(skill.id)

    skills.approve(skill.id, approved_by="owner")
    skills.activate(skill.id)
    assert skills.get(skill.id).status is SkillStatus.ACTIVE


async def test_a_destructive_step_may_not_continue_on_error(skills):
    skill = skills.capture(
        name="risky",
        description="d",
        steps=[SkillStep(seq=0, tool="delete", args={"path": "/x"}, on_error="continue")],
    )
    result = await skills.test(skill.id)
    assert not result.ok
    assert any("must not continue on error" in f for f in result.failures)


async def test_sandbox_testing_checks_that_the_tools_exist(skills):
    from thursday_tools.builtin import register_builtin_tools
    from thursday_tools.registry import ToolRegistry

    tools = ToolRegistry()
    register_builtin_tools(tools, hub=object(), memory=None, vault=None)
    registry = SkillRegistry(tools=tools)

    good = registry.capture(name="ok", description="d", steps=[SkillStep(seq=0, tool="clock")])
    assert (await registry.test(good.id)).ok

    bad = registry.capture(name="bad", description="d", steps=[SkillStep(seq=0, tool="nope")])
    result = await registry.test(bad.id)
    assert not result.ok and "not registered" in result.failures[0]


async def test_a_failing_sandbox_case_is_reported_not_swallowed(skills):
    skill = skills.capture(
        name="s",
        description="d",
        steps=[SkillStep(seq=0, tool="clock")],
        tests=[SkillTest(name="case-1")],
    )
    result = await skills.test(skill.id)
    # No sandbox executor is wired, so the case cannot pass — and does not claim to.
    assert not result.ok
    assert "case-1" in result.failures[0]


async def test_versions_are_kept_so_a_regression_can_be_rolled_back(skills):
    from thursday_automation.skills.models import SkillVersion

    skill = skills.capture(name="s", description="d", steps=[SkillStep(seq=0, tool="clock")])
    await skills.test(skill.id)
    skills.activate(skill.id)

    skills.add_version(
        skill.id, SkillVersion(steps=[SkillStep(seq=0, tool="clock")], changelog="v2")
    )
    assert skill.status is SkillStatus.DRAFT  # a new version is not live until tested

    await skills.test(skill.id, version=2)
    skills.activate(skill.id, version=2)
    assert skill.current_version == 2

    skills.rollback(skill.id, to=1)
    assert skill.current_version == 1
    assert skills.get(skill.id).version(2) is not None  # v2 is kept, not destroyed


# ------------------------------------------------------------------ dynamic agents


def spec(name: str, **overrides) -> DynamicAgentSpec:
    base = {
        "name": name,
        "goal": "grade the exam papers",
        "system_prompt": "You grade exam papers.",
        "tools": ["read_file"],
        "permissions": PermissionSet(max_level=PermissionLevel.READ),
    }
    return DynamicAgentSpec(**{**base, **overrides})


def test_a_dynamic_agent_cannot_hold_more_than_its_parent():
    factory = AgentFactory(AgentRegistry())
    agent = factory.create(
        spec("grader", permissions=PermissionSet(max_level=PermissionLevel.ADMIN)),
        task_id=new_id(),
        parent_permissions=PermissionSet(max_level=PermissionLevel.READ),
    )
    assert agent.spec.permission_ceiling is PermissionLevel.READ


def test_dynamic_agents_are_capped_per_task():
    factory = AgentFactory(AgentRegistry())
    task_id = new_id()
    for i in range(MAX_PER_TASK):
        factory.create(spec(f"worker-{i}"), task_id=task_id)
    with pytest.raises(ThursdayError, match="already created"):
        factory.create(spec("one-too-many"), task_id=task_id)


def test_dynamic_agents_cannot_nest_indefinitely():
    factory = AgentFactory(AgentRegistry())
    with pytest.raises(ThursdayError, match="nest deeper"):
        factory.create(spec("deep"), task_id=new_id(), depth=MAX_DEPTH + 1)


def test_a_dynamic_agent_does_not_outlive_its_task():
    registry = AgentRegistry()
    factory = AgentFactory(registry)
    task_id = new_id()
    factory.create(spec("temp"), task_id=task_id)
    assert registry.has("temp")

    assert factory.destroy_for_task(task_id) == 1
    assert not registry.has("temp")
    assert factory.active_for(task_id) == []
