"""Proactivity, offers, goals, reflection and recovery (§41, §46, §59, V10)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from thursday_automation.engine import ProactivityGate
from thursday_automation.offers import OfferBook
from thursday_automation.proactive import Observation, ProactiveEngine, anomalies_in
from thursday_core.briefing import Brief, DecisionJournal
from thursday_core.goals import GoalManager, Priority, PriorityQueue
from thursday_core.intent_rules import parse
from thursday_core.recovery import (
    NEVER_AUTOMATIC,
    SELF_REPAIRS,
    SelfRecovery,
    is_self_repairable,
)
from thursday_core.reflection import FeedbackLog, SelfEvaluator
from thursday_shared.enums import (
    AgentVerdict,
    NotificationPriority,
    ProactivityLevel,
    RiskLevel,
    TaskState,
)
from thursday_shared.ids import new_id

# ------------------------------------------------------------------ noticing is not doing


def test_a_read_only_reversible_local_action_may_just_be_done():
    observation = Observation(kind="device.offline", summary="x", read_only=True)
    assert observation.may_act_alone


@pytest.mark.parametrize(
    "kwargs",
    [
        {"read_only": False},  # writing a file the owner did not ask for
        {"reversible": False},
        {"external": True},
        {"risk": RiskLevel.HIGH},
    ],
)
def test_anything_else_must_be_offered(kwargs):
    """The one place this question is answered, so "safe action" cannot come to mean
    different things in different observers."""
    assert not Observation(kind="k", summary="s", **kwargs).may_act_alone


def test_preparing_a_document_is_not_a_safe_action():
    """Not because drafting is dangerous — because a file the owner did not ask for is a
    file they did not expect."""
    observation = Observation(
        kind="calendar.upcoming", summary="meeting", proposal="prepare", read_only=False
    )
    assert not observation.may_act_alone


# ------------------------------------------------------------------ the sweep


@pytest.fixture
def engine() -> ProactiveEngine:
    return ProactiveEngine(gate=ProactivityGate(ProactivityLevel.HIGH))


async def test_the_same_fact_is_raised_once(engine):
    """Said again is not new information — it is nagging."""
    engine.observe("x", lambda: [Observation(kind="task.deadline", summary="due tomorrow")])

    assert len(await engine.sweep()) == 1
    assert await engine.sweep() == []


async def test_it_can_be_raised_again_after_the_window():
    engine = ProactiveEngine(
        gate=ProactivityGate(ProactivityLevel.HIGH), repeat_window=timedelta(minutes=1)
    )
    engine.observe("x", lambda: [Observation(kind="task.deadline", summary="due")])
    start = datetime(2026, 3, 9, 9, 0, tzinfo=UTC)

    assert len(await engine.sweep(now=start)) == 1
    assert len(await engine.sweep(now=start + timedelta(minutes=5))) == 1


async def test_one_broken_observer_does_not_silence_the_others(engine):
    """A proactive layer that goes quiet because one check threw fails exactly when
    something is wrong, which is when it is most needed."""

    def broken():
        raise RuntimeError("the calendar service is down")

    engine.observe("broken", broken)
    engine.observe("fine", lambda: [Observation(kind="system.warning", summary="disk is low")])

    raised = await engine.sweep()
    assert [o.summary for o in raised] == ["disk is low"]


async def test_nothing_is_raised_when_proactivity_is_off():
    engine = ProactiveEngine(gate=ProactivityGate(ProactivityLevel.OFF))
    engine.observe("x", lambda: [Observation(kind="system.warning", summary="anything")])
    assert await engine.sweep() == []


async def test_private_findings_are_held_when_someone_else_is_there(engine):
    engine.observe(
        "x",
        lambda: [
            Observation(kind="task.deadline", summary="your medical appointment", private=True)
        ],
    )
    assert await engine.sweep(people_present=2) == []
    assert len(await engine.sweep(people_present=1)) == 1


async def test_an_async_observer_works_too(engine):
    async def observe():
        return [Observation(kind="calendar.upcoming", summary="meeting at nine")]

    engine.observe("async", observe)
    assert len(await engine.sweep()) == 1


# ------------------------------------------------------------------ anomalies


def test_blank_and_negative_values_are_flagged():
    """A cheap, explainable test. "2.7 standard deviations from the mean" is a claim the
    owner cannot check at a glance, and one they cannot check is one they learn to ignore."""
    found = anomalies_in(
        {
            "rows": [
                {"name": "a", "score": 80},
                {"name": "b", "score": None},
                {"name": "c", "score": -5},
            ]
        },
        task_title="Grade report",
    )
    assert len(found) == 1
    assert "2 รายการ" in found[0].summary


def test_clean_data_raises_nothing():
    assert anomalies_in({"rows": [{"name": "a", "score": 80}]}) == []


# ------------------------------------------------------------------ offers


@pytest.fixture
def offers() -> OfferBook:
    return OfferBook()


def test_one_yes_answers_one_question(offers):
    """An owner saying yes to a list has agreed to something, and nobody — including them —
    could say which."""
    offers.make("prepare for the meeting")
    offers.make("tidy the downloads folder")

    accepted = offers.accept()
    assert accepted is not None
    assert accepted.text == "tidy the downloads folder"  # the one just asked
    assert len(offers.pending()) == 1


def test_an_expired_offer_cannot_be_accepted(offers):
    """ "Shall I prepare for tomorrow's meeting?" is a dead question the day after."""
    start = datetime(2026, 3, 9, 9, 0, tzinfo=UTC)
    offers.make("prepare for the meeting", now=start)

    assert offers.accept(now=start + timedelta(days=2)) is None


def test_declining_settles_the_question(offers):
    offers.make("tidy up")
    declined = offers.decline()
    assert declined is not None and declined.accepted is False
    assert offers.pending() == []


def test_answering_with_nothing_outstanding_is_not_an_error(offers):
    assert offers.accept() is None


# ------------------------------------------------------------------ answering


@pytest.mark.parametrize("said", ["ทำเลย", "ทำเลยครับ", "เอาเลย", "ตกลง", "ok", "yes", "go ahead"])
def test_yes_is_recognised(said):
    match = parse(said)
    assert match is not None and str(match.intent.kind) == "APPROVE"


@pytest.mark.parametrize("said", ["ไม่ต้อง", "ไม่เอาครับ", "no thanks", "not now"])
def test_no_is_recognised(said):
    match = parse(said)
    assert match is not None and str(match.intent.kind) == "DECLINE"


@pytest.mark.parametrize(
    "said",
    [
        "ใช่ไหมครับ",  # a question, not agreement
        "confirm the booking",  # an instruction to go and confirm something
        "ตกลงกันไว้ว่าจะประชุม",  # "we agreed to meet"
        "no idea what that is",
    ],
)
def test_a_sentence_that_merely_contains_yes_or_no_is_not_an_answer(said):
    """Answering is a complete short utterance. Anchoring at both ends is what keeps an
    instruction out of a rule that means "yes, the thing you just asked me"."""
    match = parse(said)
    assert match is None or str(match.intent.kind) not in ("APPROVE", "DECLINE")


# ------------------------------------------------------------------ goals and priority


def test_a_goal_is_achieved_when_its_missions_are():
    """Derived rather than declared, so the two can never disagree."""
    goals = GoalManager()
    goal = goals.add_goal("Ship the term reports", priority=Priority.HIGH)
    first = goals.add_mission(goal.id, "collect the marks")
    second = goals.add_mission(goal.id, "write the reports")

    goals.complete_mission(first.id)
    assert goals.progress(goal.id) == 0.5
    assert goal.open

    goals.complete_mission(second.id)
    assert goals.progress(goal.id) == 1.0
    assert not goal.open


def test_a_task_inherits_its_goals_priority():
    """ "This is HIGH because it serves a HIGH goal" is an answer; "this is 7" is not."""
    goals = GoalManager()
    goal = goals.add_goal("Term reports", priority=Priority.CRITICAL)

    class _Task:
        priority = 5  # the old integer, meaning nothing
        goal_id = goal.id

    queue = PriorityQueue(tasks=None, goals=goals)
    assert queue.priority_of(_Task()) is Priority.CRITICAL


async def test_preemption_pauses_rather_than_cancels(container):
    """Otherwise "higher priority" quietly means "destroys lower-priority work"."""
    low = await container.tasks.create(title="tidy up", objective="tidy")
    await container.tasks.transition(low.id, TaskState.PLANNING)
    await container.tasks.transition(low.id, TaskState.RUNNING)

    urgent = await container.tasks.create(title="fix the outage", objective="fix")
    urgent.priority = Priority.CRITICAL

    paused = await container.priorities.preempt(urgent)
    assert [t.title for t in paused] == ["tidy up"]
    # PAUSED, which the task state machine defines as resuming where it stopped.
    assert container.tasks.get(low.id).status is TaskState.PAUSED


async def test_ordinary_work_does_not_preempt(container):
    """Two NORMAL tasks do not fight; the second waits."""
    low = await container.tasks.create(title="tidy up", objective="tidy")
    await container.tasks.transition(low.id, TaskState.PLANNING)
    await container.tasks.transition(low.id, TaskState.RUNNING)

    other = await container.tasks.create(title="another thing", objective="thing")
    other.priority = Priority.NORMAL

    assert await container.priorities.preempt(other) == []
    assert container.tasks.get(low.id).status is TaskState.RUNNING


async def test_a_preempted_task_can_be_resumed(container):
    low = await container.tasks.create(title="tidy up", objective="tidy")
    await container.tasks.transition(low.id, TaskState.PLANNING)
    await container.tasks.transition(low.id, TaskState.RUNNING)

    urgent = await container.tasks.create(title="urgent", objective="urgent")
    urgent.priority = Priority.CRITICAL
    await container.priorities.preempt(urgent)

    await container.priorities.resume(low.id)
    assert container.tasks.get(low.id).status is TaskState.RUNNING


# ------------------------------------------------------------------ the journal


def test_a_decision_records_what_was_not_chosen():
    """The options not taken are what turn a log line into something re-examinable."""
    journal = DecisionJournal()
    entry = journal.record(
        "use pgvector rather than a separate vector database",
        reason="one store, one transaction, one backup",
        alternatives=["Pinecone", "Qdrant", "Chroma"],
        source="owner",
    )
    assert "Pinecone" in entry.describe("en")
    assert "by owner" in entry.describe("en")


def test_the_journal_can_be_read_by_source_and_date():
    journal = DecisionJournal()
    journal.record("a", reason="r", source="owner")
    journal.record("b", reason="r", source="thursday")
    assert len(journal.entries(source="owner")) == 1
    assert len(journal.entries(since=datetime.now(UTC) + timedelta(hours=1))) == 0


def test_an_empty_brief_says_so_rather_than_printing_headings():
    """A morning list of "none" is one people learn to skip, and the skipping generalises
    to the section that one day is not empty."""
    brief = Brief(when=datetime.now(UTC).date())
    assert brief.empty
    assert "Nothing to report" in brief.render("en")


# ------------------------------------------------------------------ self-evaluation


class _Verification:
    def __init__(self, passed: bool, verdict=AgentVerdict.PASS) -> None:
        self.passed = passed
        self.verdict = verdict


class _Step:
    def __init__(self, name: str, attempt: int = 1) -> None:
        self.name = name
        self.attempt = attempt


class _Plan:
    def __init__(self, steps) -> None:
        self.steps = steps


class _FinishedTask:
    def __init__(
        self, *, status=TaskState.COMPLETED, passed=True, attempt=1, verdict=AgentVerdict.PASS
    ):
        self.id = new_id()
        self.status = status
        self.verification = _Verification(passed, verdict)
        self.plan = _Plan([_Step("data", attempt)])
        self.assigned_agent = None


def test_a_clean_run_is_recognised():
    review = SelfEvaluator().review(_FinishedTask())
    assert review.clean
    assert review.notes == []


def test_a_retried_run_is_not_clean():
    review = SelfEvaluator().review(_FinishedTask(attempt=2))
    assert not review.clean
    assert "took 2 attempts" in review.notes


def test_a_corrected_result_is_not_clean():
    """The strongest available signal that the output was not what the owner wanted, and
    one nobody has to be asked for."""
    review = SelfEvaluator().review(_FinishedTask(), corrected=True)
    assert not review.clean
    assert "the owner changed the result afterwards" in review.notes


def test_agent_scores_are_a_record_not_a_ranking():
    evaluator = SelfEvaluator()
    evaluator.review(_FinishedTask())
    evaluator.review(_FinishedTask(attempt=3))
    assert evaluator.agent_scores()["data"] == 0.5


# ------------------------------------------------------------------ feedback


def test_one_correction_changes_nothing():
    """A single "no" might mean never, or not for this document, or not today. Storing the
    strongest reading of an ambiguous signal is how an assistant quietly stops doing things."""
    log = FeedbackLog()
    log.record("report format", said="แบบนี้ไม่เอา")
    assert log.proposals() == []


def test_a_repeated_correction_becomes_a_question_not_a_preference():
    log = FeedbackLog(repeats=3)
    for _ in range(3):
        log.record("report format", said="แบบนี้ไม่เอา")

    proposals = log.proposals()
    assert len(proposals) == 1
    assert proposals[0].occurrences == 3
    # A question. An agent may not write the owner's preferences (PART 76).
    assert "Shall I remember" in proposals[0].describe("en")


def test_old_corrections_stop_counting():
    """A complaint from two months ago about a format nobody uses should not accumulate."""
    log = FeedbackLog(repeats=2, window=timedelta(days=7))
    old = datetime.now(UTC) - timedelta(days=30)
    log.record("report format", now=old)
    log.record("report format", now=old)
    assert log.proposals() == []


def test_a_proposal_is_made_once():
    log = FeedbackLog(repeats=2)
    log.record("format")
    log.record("format")
    log.mark_proposed("format")
    assert log.proposals() == []


# ------------------------------------------------------------------ self-recovery


@pytest.mark.parametrize("action", sorted(SELF_REPAIRS))
def test_allowed_repairs_restore_a_capability(action):
    assert is_self_repairable(action)


@pytest.mark.parametrize("action", sorted(NEVER_AUTOMATIC))
def test_forbidden_repairs_change_what_thursday_may_do(action):
    """A system that can widen its own permissions to fix itself has no permission model,
    only a delay before it decides it needs more."""
    assert not is_self_repairable(action)


def test_an_unlisted_repair_is_refused():
    """Fail-closed: the other order lets a repair nobody sanctioned run on the strength of
    not having been thought of."""
    assert not is_self_repairable("do_whatever_it_takes")


def test_a_forbidden_repair_cannot_even_be_wired_up():
    """Refused at registration, because a forbidden repair that merely is never invoked is
    one line away from being invoked."""
    recovery = SelfRecovery()
    with pytest.raises(PermissionError, match="may never be performed automatically"):
        recovery.register("disable_protection", lambda: None)


async def test_a_repair_is_attempted_and_reported():
    recovery = SelfRecovery()
    calls = []
    recovery.register("reconnect_node", lambda: calls.append(1))

    outcome = await recovery.repair("Office-PC", "reconnect_node")
    assert outcome.ok and outcome.attempted
    assert calls == [1]


async def test_a_forbidden_repair_is_refused_at_call_time_too():
    outcome = await SelfRecovery().repair("vault", "rotate_credential")
    assert not outcome.attempted
    assert "needs a person" in outcome.reason


async def test_recovery_gives_up_rather_than_looping():
    """A recovery that repeats for ever is an outage with a busy loop, and it hides the
    failure from the one person who could fix it."""
    recovery = SelfRecovery(max_attempts=2)
    recovery.register("restart_worker", lambda: None)

    assert (await recovery.repair("worker", "restart_worker")).ok
    assert (await recovery.repair("worker", "restart_worker")).ok
    third = await recovery.repair("worker", "restart_worker")
    assert not third.attempted
    assert "cannot fix" in third.reason
    assert recovery.exhausted() == ["worker"]


async def test_attempts_reset_after_a_quiet_period():
    """Otherwise a system up for a month used its three attempts in March and can never
    self-heal again."""
    recovery = SelfRecovery(max_attempts=1, window=timedelta(minutes=10))
    recovery.register("switch_model", lambda: None)
    start = datetime(2026, 3, 9, 9, 0, tzinfo=UTC)

    assert (await recovery.repair("models", "switch_model", now=start)).ok
    assert not (await recovery.repair("models", "switch_model", now=start)).attempted
    assert (await recovery.repair("models", "switch_model", now=start + timedelta(hours=1))).ok


async def test_a_failing_repair_is_reported_rather_than_raised():
    recovery = SelfRecovery()

    def broken():
        raise RuntimeError("the node is not answering")

    recovery.register("reconnect_node", broken)
    outcome = await recovery.repair("PC", "reconnect_node")
    assert outcome.attempted and not outcome.ok
    assert "not answering" in outcome.reason


def test_the_gate_and_observation_priorities_line_up():
    """A sanity check on the two enums meeting: an IMPORTANT observation must be
    expressible to the gate without translation."""
    gate = ProactivityGate(ProactivityLevel.NORMAL)
    allowed, _ = gate.allows(NotificationPriority.IMPORTANT)
    assert allowed
