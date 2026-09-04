"""Teaching in the moment, without nagging (ADAPTIVE ONBOARDING) — Sprint 71.

§50 wants a scored tip engine; §51 wants a cooldown; §7 wants a frequency dial. Written
naively those three argue: a score high enough always wins, and "occasionally" becomes
"whenever the number is big".

The ordering is the design — ceiling, then shared gate, then cooldown, then score — and the
score only ever chooses *which* tip, never whether to speak. So most of these tests are about
silence: the correct answer to "is there something worth saying" is usually no.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from thursday_core import tips as tips_module
from thursday_core.learning import (
    USES_FOR_LEARNED,
    Familiarity,
    LearningRecord,
    TeachingFrequency,
)
from thursday_core.plain import leaks
from thursday_core.tips import (
    COOLDOWN,
    DISMISSED_FOR,
    MAX_INTRODUCTIONS,
    USES_BEFORE_UPGRADE,
    TipEngine,
    teach_from_error,
)

NOW = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)


def _engine(frequency=TeachingFrequency.NORMAL, gate=None) -> TipEngine:
    return TipEngine(LearningRecord(frequency=frequency), gate=gate)


def _used(engine: TipEngine, capability: str, times: int, *, now=NOW) -> None:
    for i in range(times):
        engine._record.used(capability, now=now + timedelta(seconds=i))


# ------------------------------------------------------------- the shipped default works


def test_a_tip_can_actually_fire_on_a_default_install(container):
    """The bug this test exists for, and the reason it is first.

    The first version sent tips through the gate at LOW priority, which reads well — a tip
    *should* be the first casualty of a busy hour. But `ProactivityGate` only lets LOW
    through at proactivity HIGH, and the shipped default is NORMAL. So `teaching: normal`
    promised occasional tips and delivered none, silently, on every default install: two
    settings that each looked right and were wrong together (ADR 0049's lesson again).
    """
    assert container.tips.may_speak()[0] is True

    for _ in range(USES_BEFORE_UPGRADE):
        container.learning.used("file_search")
    assert container.tips.after(container, capability="file_search") is not None


def test_turning_teaching_down_really_does_make_tips_the_first_casualty(container):
    """The other half of that fix: the two dials compose rather than one silently voiding
    the other. At LOW, a tip competes at LOW priority and normal proactivity drops it."""
    container.learning.frequency = TeachingFrequency.LOW
    allowed, why = container.tips.may_speak()
    assert allowed is False
    assert "LOW" in why or "level" in why


# ------------------------------------------------------------------ §7/§39 the ceiling


@pytest.mark.parametrize("frequency", [TeachingFrequency.OFF, TeachingFrequency.ON_REQUEST])
def test_teaching_off_means_off_whatever_the_score_would_be(container, frequency):
    """Checked before anything is scored, so there is no number that reaches past it."""
    container.learning.frequency = frequency
    _used(container.tips, "file_search", USES_BEFORE_UPGRADE * 3)
    assert container.tips.may_speak()[0] is False
    assert container.tips.after(container, capability="file_search") is None


def test_the_ceiling_is_checked_before_the_score(container):
    """Structural: `may_speak` takes no capability, so it cannot be influenced by how good
    a particular tip would be."""
    import inspect

    assert set(inspect.signature(container.tips.may_speak).parameters) == {"now"}


# ------------------------------------------------------------------------ §51 cooldown


def test_only_one_tip_per_stretch_of_work(container):
    _used(container.tips, "file_search", USES_BEFORE_UPGRADE)
    _used(container.tips, "conversation", USES_BEFORE_UPGRADE)

    first = container.tips.after(container, capability="file_search", now=NOW)
    assert first is not None

    soon = container.tips.after(
        container, capability="conversation", now=NOW + timedelta(minutes=5)
    )
    assert soon is None


def test_and_another_once_the_cooldown_has_passed(container):
    _used(container.tips, "file_search", USES_BEFORE_UPGRADE)
    _used(container.tips, "conversation", USES_BEFORE_UPGRADE)

    container.tips.after(container, capability="file_search", now=NOW)
    later = container.tips.after(
        container, capability="conversation", now=NOW + COOLDOWN + timedelta(minutes=1)
    )
    assert later is not None


def test_a_tip_never_follows_work_that_failed(container):
    """Somebody who just watched something fail is not in the mood to be taught a different
    feature; a tip there reads as changing the subject."""
    _used(container.tips, "file_search", USES_BEFORE_UPGRADE)
    assert container.tips.after(container, capability="file_search", succeeded=False) is None


# ------------------------------------------------------------------------ §66 dismissal


def test_a_dismissed_capability_is_not_raised_again(container):
    """§66's acceptance test, verbatim: "If dismissed: do not repeatedly ask.\""""
    _used(container.tips, "file_search", USES_BEFORE_UPGRADE)
    tip = container.tips.after(container, capability="file_search", now=NOW)
    assert tip is not None

    container.tips.dismiss(tip.capability, now=NOW)

    later = container.tips.after(container, capability="file_search", now=NOW + COOLDOWN * 2)
    assert later is None


def test_a_dismissal_is_about_the_capability_not_the_wording(container):
    """Recorded against the capability, so no rephrasing gets past it — "not interested in
    skills" is about skills however many ways Thursday finds to raise them."""
    container.tips.dismiss("skills", now=NOW)
    score, why = container.tips.score(container, "skills", after="file_search", now=NOW)
    assert score == 0.0
    assert "dismiss" in why


def test_a_dismissal_is_not_permanent(container):
    """ "Not now" in week one is a different sentence from "never"."""
    _used(container.tips, "file_search", USES_BEFORE_UPGRADE, now=NOW)
    container.tips.dismiss("skills", now=NOW)
    much_later = NOW + DISMISSED_FOR + timedelta(days=1)
    score, _ = container.tips.score(container, "skills", after="file_search", now=much_later)
    assert score > 0


def test_repeating_a_message_that_is_not_landing_stops(container):
    """Three introductions with no use is a message that is not working, and saying it a
    fourth time is nagging."""
    for _ in range(MAX_INTRODUCTIONS):
        container.learning.introduced("skills", now=NOW)
    _used(container.tips, "file_search", USES_BEFORE_UPGRADE, now=NOW)
    score, why = container.tips.score(container, "skills", after="file_search", now=NOW)
    assert score == 0.0
    assert "introduced" in why


# ------------------------------------------------------------------------ §50 scoring


def test_availability_is_a_veto_rather_than_a_weight(container):
    """Teaching a camera to a machine with no camera is §12's failure however relevant it
    would otherwise be — so it is not a term in a sum that something else can outweigh."""
    score, why = container.tips.score(container, "vision", now=NOW)
    assert score == 0.0
    assert "available" in why


@pytest.mark.parametrize("uses", range(USES_BEFORE_UPGRADE))
def test_nothing_is_offered_until_the_owner_has_done_it_often_enough(container, uses):
    """§6's offer earns its welcome from frequency, and says so in its own words:
    "คุณใช้ผมหาไฟล์บ่อย". Offered after one search that sentence is untrue, and a tip whose
    text is false is worse than no tip. So the threshold is a veto rather than a small
    penalty — an e2e run is what caught this, firing at one use with a score of 0.79.
    """
    for i in range(uses):
        container.learning.used("file_search", now=NOW + timedelta(seconds=i))
    score, why = container.tips.score(container, "skills", after="file_search", now=NOW)
    assert score == 0.0
    assert "only" in why


def test_and_is_offered_once_it_has(container):
    _used(container.tips, "file_search", USES_BEFORE_UPGRADE, now=NOW)
    score, why = container.tips.score(container, "skills", after="file_search", now=NOW)
    assert score > 0
    assert str(USES_BEFORE_UPGRADE) in why


def test_a_capability_already_mentioned_scores_below_one_never_raised(container):
    """The first mention is the informative one, so an untouched capability outranks one
    Thursday has already brought up — among candidates that all cleared the threshold."""
    fresh = LearningRecord(frequency=TeachingFrequency.NORMAL)
    mentioned = LearningRecord(frequency=TeachingFrequency.NORMAL)
    for i in range(USES_BEFORE_UPGRADE):
        fresh.used("file_search", now=NOW + timedelta(seconds=i))
        mentioned.used("file_search", now=NOW + timedelta(seconds=i))
    mentioned.introduced("skills", now=NOW)

    high, _ = TipEngine(fresh).score(container, "skills", after="file_search", now=NOW)
    low, _ = TipEngine(mentioned).score(container, "skills", after="file_search", now=NOW)
    assert high > low > 0


def test_something_already_learned_is_not_taught(container):
    record = LearningRecord(frequency=TeachingFrequency.NORMAL)
    for i in range(USES_FOR_LEARNED):
        record.used("skills", now=NOW + timedelta(seconds=i))
    assert record.knows("skills", now=NOW) >= Familiarity.LEARNED

    score, why = TipEngine(record).score(container, "skills", after="file_search", now=NOW)
    assert score == 0.0
    assert "already knows" in why


def test_the_score_never_decides_whether_to_speak_only_which(container):
    """The tip that scores highest still gets nothing if teaching is off. This is the
    property that keeps a tunable number from becoming a reason to interrupt."""
    container.learning.frequency = TeachingFrequency.OFF
    _used(container.tips, "file_search", USES_BEFORE_UPGRADE * 5, now=NOW)
    best, _ = container.tips.score(container, "skills", after="file_search", now=NOW)
    assert best > 0
    assert container.tips.after(container, capability="file_search", now=NOW) is None


# -------------------------------------------------------------------- §41 micro-lessons


def test_a_tip_carries_one_idea_and_a_way_to_act_on_it(container):
    _used(container.tips, "file_search", USES_BEFORE_UPGRADE, now=NOW)
    tip = container.tips.after(container, capability="file_search", now=NOW)
    assert tip.text and tip.try_this
    assert len(tip.text) < 200
    assert tip.text.count("—") <= 1


def test_every_tip_is_written_in_the_owners_language():
    for capability, (_taught, text, try_this) in tips_module._AFTER_USING.items():
        assert leaks(text) == [], capability
        assert leaks(try_this) == [], capability


def test_a_tip_is_always_dismissible(container):
    _used(container.tips, "file_search", USES_BEFORE_UPGRADE, now=NOW)
    rendered = container.tips.after(container, capability="file_search", now=NOW).render()
    assert rendered["dismiss"]


def test_offering_a_tip_only_reaches_discovered(container):
    """Sprint 67's rule once more: Thursday mentioning skills is not the owner knowing them."""
    _used(container.tips, "file_search", USES_BEFORE_UPGRADE, now=NOW)
    tip = container.tips.after(container, capability="file_search", now=NOW)
    assert container.learning.knows(tip.capability, now=NOW) is Familiarity.DISCOVERED


def test_every_tip_teaches_a_capability_the_catalogue_knows():
    from thursday_core.catalogue import FEATURES_BY_KEY

    for used, (taught, _text, _try) in tips_module._AFTER_USING.items():
        assert used in FEATURES_BY_KEY, used
        assert taught in FEATURES_BY_KEY, taught


# ------------------------------------------------------------------ §27 error as teaching


def test_a_refusal_names_the_next_step_rather_than_the_diagnosis(container):
    """§27's own example: not "camera permission denied" but where to turn it on."""
    teaching = teach_from_error(container, "vision")
    assert teaching is not None
    assert "กล้อง" in teaching["problem"]
    assert teaching["fix"]
    assert leaks(str(teaching)) == []


def test_error_teaching_is_not_gated_by_teaching_frequency(container):
    """The owner asked for something and did not get it. Telling them why is answering, not
    teaching — §39's ceiling is about Thursday speaking *unprompted*."""
    container.learning.frequency = TeachingFrequency.OFF
    assert teach_from_error(container, "vision") is not None


def test_an_unmapped_failure_says_nothing_rather_than_guessing(container):
    assert teach_from_error(container, "quantum_entanglement") is None
