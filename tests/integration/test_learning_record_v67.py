"""What the owner knows, and what may change that record (ADAPTIVE ONBOARDING) — Sprint 67.

The spec gives two records (§8, §9) and a handful of settings (§7, §38, §39, §45). What
makes them worth testing is not the storage — it is the set of things that are *not* allowed
to move them.

A tutor that overstates what the owner knows stops explaining, and the failure is silent:
they simply stop being helped and have no way to name what went wrong. So the tests below are
mostly about refusing to promote.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from thursday_core.learning import (
    DECAY_AFTER,
    MASTERY_QUIET,
    USES_FOR_LEARNED,
    USES_FOR_MASTERED,
    Familiarity,
    LearningRecord,
    TeachingFrequency,
    TutorialStatus,
    Verbosity,
)

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)


def _record(**kw) -> LearningRecord:
    return LearningRecord(**kw)


# --------------------------------------------------------------- being told is not knowing


def test_a_capability_nobody_mentioned_is_not_discovered():
    assert _record().knows("gesture") is Familiarity.NOT_DISCOVERED


def test_being_told_about_a_feature_advances_no_further_than_discovered():
    """The central rule of this module. Thursday mentioning the camera is evidence about
    what Thursday said, not about what the owner can do — and a record that conflates the
    two goes quiet on somebody who never understood a word of it."""
    record = _record()
    for _ in range(20):
        record.introduced("vision", now=NOW)
    assert record.knows("vision", now=NOW) is Familiarity.DISCOVERED


def test_using_something_once_reaches_tried_and_stops_there():
    record = _record()
    assert record.used("file_search", now=NOW) is Familiarity.TRIED


def test_learned_needs_more_than_one_success():
    """The first success may have been Thursday's own tip read back. A tutor that stops
    helping after one lucky run stops too early."""
    record = _record()
    for i in range(USES_FOR_LEARNED):
        state = record.used("file_search", now=NOW + timedelta(days=i))
    assert USES_FOR_LEARNED > 1
    assert state is Familiarity.LEARNED


def test_a_failed_attempt_counts_as_tried_but_never_climbs():
    """A capability the owner keeps failing at is the last one to go quiet about."""
    record = _record()
    for i in range(USES_FOR_MASTERED * 2):
        record.used("gesture", ok=False, now=NOW + timedelta(days=i))
    assert record.knows("gesture", now=NOW) is Familiarity.TRIED
    assert record.entry("gesture").uses == 0


# ------------------------------------------------------------------------ mastery is dear


def test_mastery_cannot_be_reached_by_use_alone_while_thursday_is_still_helping():
    """MASTERED is the state that *removes* explanation, so it needs both halves: the owner
    doing it repeatedly, and Thursday having managed to stay out of the way."""
    record = _record()
    record.introduced("voice", now=NOW)  # taught today
    for i in range(USES_FOR_MASTERED + 3):
        record.used("voice", now=NOW + timedelta(hours=i))
    assert record.knows("voice", now=NOW) is Familiarity.LEARNED


def test_mastery_arrives_once_the_owner_has_been_left_alone_with_it():
    record = _record()
    record.introduced("voice", now=NOW)
    later = NOW + MASTERY_QUIET + timedelta(days=1)
    for i in range(USES_FOR_MASTERED):
        state = record.used("voice", now=later + timedelta(hours=i))
    assert state is Familiarity.MASTERED


def test_familiarity_decays_when_something_goes_unused():
    """A profile that only climbs eventually claims the owner is expert at everything,
    including what they last touched in March."""
    record = _record()
    later = NOW + MASTERY_QUIET + timedelta(days=1)
    for i in range(USES_FOR_MASTERED):
        record.used("gesture", now=later + timedelta(hours=i))
    assert record.knows("gesture", now=later) is Familiarity.MASTERED

    stale = later + DECAY_AFTER + timedelta(days=1)
    assert record.knows("gesture", now=stale) is Familiarity.LEARNED


def test_decay_never_erases_that_they_tried_it():
    """Forgetting how is plausible. Never having heard of it is not, and rewinding to
    NOT_DISCOVERED would make Thursday re-introduce something they have used."""
    record = _record()
    record.used("memory", now=NOW)
    assert record.knows("memory", now=NOW + DECAY_AFTER * 5) is Familiarity.TRIED


# ---------------------------------------------------------------------- teaching ceilings


@pytest.mark.parametrize("frequency", [TeachingFrequency.OFF, TeachingFrequency.ON_REQUEST])
def test_teaching_off_and_on_request_forbid_all_unprompted_teaching(frequency):
    """§39. A ceiling, not an input to a score — no relevance number reaches past it."""
    assert _record(frequency=frequency).may_teach_unprompted() is False


@pytest.mark.parametrize(
    "frequency", [TeachingFrequency.LOW, TeachingFrequency.NORMAL, TeachingFrequency.HIGH]
)
def test_the_other_levels_permit_it(frequency):
    assert _record(frequency=frequency).may_teach_unprompted() is True


def test_the_frequencies_are_ordered_so_a_threshold_replaces_a_lookup():
    assert (
        TeachingFrequency.OFF
        < TeachingFrequency.ON_REQUEST
        < TeachingFrequency.LOW
        < TeachingFrequency.NORMAL
        < TeachingFrequency.HIGH
    )


def test_the_shipped_default_is_normal():
    """§7 names the default outright, and Sprint 62's lesson was that the shipped
    configuration is the product — so this asks the *file* a real install reads, not the
    code default underneath it, and asserts the parsed value rather than its spelling."""
    from thursday_core.config import get_settings

    assert get_settings().teaching_frequency is TeachingFrequency.NORMAL


def test_a_misspelled_teaching_setting_goes_quiet_rather_than_loud():
    """The safe reading of an instruction nobody can parse. Somebody editing this line is
    trying to turn teaching *down*, and the bad failure here is Thursday talking over a
    person who asked it not to."""
    from thursday_core.config import Settings

    assert Settings(data_dir="var", teaching="nrmal").teaching_frequency is TeachingFrequency.OFF


# ------------------------------------------------------------------------- §45 verbosity


def test_a_new_owner_gets_beginner():
    assert _record().verbosity(now=NOW) is Verbosity.BEGINNER


def test_verbosity_reads_breadth_not_depth():
    """Somebody fluent with files and new to everything else is not an expert, and treating
    them as one is how a system stops explaining the parts they still need."""
    record = _record()
    quiet = NOW + MASTERY_QUIET + timedelta(days=1)
    for i in range(USES_FOR_MASTERED * 3):
        record.used("file_search", now=quiet + timedelta(hours=i))
    assert record.knows("file_search", now=quiet) is Familiarity.MASTERED
    assert record.verbosity(now=quiet) is not Verbosity.EXPERT


def test_expert_needs_several_capabilities_mastered():
    record = _record()
    quiet = NOW + MASTERY_QUIET + timedelta(days=1)
    for capability in ("voice", "file_search", "memory", "computer", "agents"):
        for i in range(USES_FOR_MASTERED):
            record.used(capability, now=quiet + timedelta(hours=i))
    assert record.verbosity(now=quiet) is Verbosity.EXPERT


def test_the_owner_can_override_what_thursday_inferred():
    """§45: "ผู้ใช้ override ได้". An inference the owner cannot correct is a decision
    they were not party to."""
    record = _record()
    record.verbosity_override = Verbosity.EXPERT
    assert record.verbosity(now=NOW) is Verbosity.EXPERT

    record.verbosity_override = Verbosity.BEGINNER
    quiet = NOW + MASTERY_QUIET + timedelta(days=1)
    for capability in ("voice", "file_search", "memory", "computer", "agents"):
        for i in range(USES_FOR_MASTERED):
            record.used(capability, now=quiet + timedelta(hours=i))
    assert record.verbosity(now=quiet) is Verbosity.BEGINNER


# ------------------------------------------------------------------------ §9 progress


def test_a_tutorial_records_where_it_got_to():
    record = _record()
    record.start("basics", now=NOW)
    record.advance("basics", "say-something", now=NOW)
    record.advance("basics", "open-an-app", now=NOW)
    progress = record.progress("basics")

    assert progress.status is TutorialStatus.IN_PROGRESS
    assert progress.completed_steps == ["say-something", "open-an-app"]
    assert progress.current_step == 2
    assert progress.confidence == 1.0
    assert not progress.finished


def test_confidence_reports_how_it_went_rather_than_grading_it():
    record = _record()
    record.start("basics", now=NOW)
    record.advance("basics", "a", now=NOW)
    record.skip_step("basics", "b", now=NOW)
    assert record.progress("basics").confidence == pytest.approx(0.5)


def test_completing_a_tutorial_is_final_and_timestamped():
    record = _record()
    record.start("basics", now=NOW)
    record.complete("basics", now=NOW)
    progress = record.progress("basics")
    assert progress.status is TutorialStatus.COMPLETED
    assert progress.completed_at == NOW
    assert progress.finished


def test_skipping_is_finished_too_so_nothing_reopens_it():
    """§2 offers "ข้ามก่อน", and a skip that leaves the tutorial pending is a tutorial that
    comes back tomorrow."""
    record = _record()
    record.skip("intro", now=NOW)
    assert record.progress("intro").finished


def test_a_step_is_never_double_counted():
    record = _record()
    record.start("basics", now=NOW)
    record.advance("basics", "a", now=NOW)
    record.advance("basics", "a", now=NOW)
    assert record.progress("basics").completed_steps == ["a"]


# ------------------------------------------------------------------------- §38 resetting


def test_reset_tips_forgets_what_was_dismissed_but_not_what_was_done():
    """ "Show beginner tips again" restores the offers. It must not rewrite the owner's
    history of what they actually used — that is their record, not the tutor's."""
    record = _record()
    record.used("file_search", now=NOW)
    record.used("file_search", now=NOW)
    record.introduced("gesture", now=NOW)
    record.dismissed("gesture", now=NOW)

    record.reset_tips()

    assert record.entry("gesture").dismissed is False
    assert record.entry("gesture").introductions == 0
    assert record.knows("file_search", now=NOW) is Familiarity.LEARNED
    assert record.entry("file_search").uses == 2


def test_reset_tutorials_clears_lessons_only():
    record = _record()
    record.used("voice", now=NOW)
    record.start("basics", now=NOW)
    assert record.reset_tutorials() == 1
    assert record.all_progress() == {}
    assert record.knows("voice", now=NOW) is Familiarity.TRIED


def test_restart_introduction_forgets_everything():
    record = _record()
    record.used("voice", now=NOW)
    record.start("basics", now=NOW)
    record.reset_all()
    assert record.all_capabilities() == {}
    assert record.all_progress() == {}
    assert record.verbosity(now=NOW) is Verbosity.BEGINNER


# --------------------------------------------------------------------------- §66 dismissal


def test_a_dismissal_is_recorded_separately_from_not_knowing():
    """Declining to learn gestures is not the same as not knowing they exist. Collapsing
    the two means either re-offering what was refused, or never mentioning it again."""
    record = _record()
    record.introduced("gesture", now=NOW)
    record.dismissed("gesture", now=NOW)
    assert record.entry("gesture").dismissed is True
    assert record.knows("gesture", now=NOW) is Familiarity.DISCOVERED


# ------------------------------------------------------------------------------- §42 view


def test_the_progress_view_has_nothing_to_accumulate():
    """ "ห้ามบังคับสะสมคะแนน". No score, no total, no percentage — a list of what has been
    used and what has not."""
    record = _record()
    record.used("voice", now=NOW)
    record.introduced("gesture", now=NOW)
    snapshot = record.snapshot(now=NOW)

    assert snapshot["used"] == ["voice"]
    assert "score" not in snapshot
    assert "points" not in snapshot
    assert "total" not in snapshot
    assert "percent" not in snapshot
