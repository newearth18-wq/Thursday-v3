"""Lessons that end when the machine proves it (ADAPTIVE ONBOARDING) — Sprint 69.

§4 gives the loop and VERIFY is the step that decides whether this is a tutorial or a
slideshow. A lesson that congratulates somebody on a step that did not work has taught them a
thing that does not happen, and they find out at the moment they first needed it — which is
the same failure Sprint 64 designed the setup wizard against, one layer up.

So most of what is tested here is refusal: to pass a step on a plausible-looking reply, to be
told a step succeeded, to offer a lesson the machine cannot run.
"""

from __future__ import annotations

import inspect

import pytest
from thursday_core import lessons
from thursday_core.learning import Familiarity, LearningRecord, TutorialStatus
from thursday_core.lessons import (
    LESSONS,
    LESSONS_BY_ID,
    QUICK_INTRO,
    LessonRunner,
    Stage,
    available,
    next_lesson,
    path,
    runnable,
)
from thursday_core.plain import leaks


@pytest.fixture
def record() -> LearningRecord:
    return LearningRecord()


@pytest.fixture
def runner(record) -> LessonRunner:
    return LessonRunner(record)


# ------------------------------------------------------------------ VERIFY is an observation


def test_there_is_no_parameter_anywhere_for_asserting_a_step_succeeded(runner):
    """The structural version of the rule. A completion flag a caller can set is a
    completion flag a caller will set — eventually, from a retry handler at 3am. Same shape
    as the updater having no parameter for a URL (ADR 0033)."""
    attempt = set(inspect.signature(runner.attempt).parameters)
    assert attempt == {"container", "lesson_id", "evidence", "now"}
    for forbidden in ("passed", "ok", "success", "force", "verified", "skip_verification"):
        assert forbidden not in attempt


async def test_a_step_does_not_pass_on_a_reply_that_merely_looked_right(runner, container):
    """The device step's evidence says the command returned without raising and *nothing
    was observed*. `ok` without `verified` is exactly the lie ADR 0012 exists to prevent."""
    runner.start(container, "say-something")
    result = await runner.attempt(container, "say-something", {"reply": ""})
    assert result.passed is False


async def test_an_app_step_needs_the_node_to_have_looked(container, office_pc, record):
    runner = LessonRunner(record)
    runner.start(container, "open-an-app")

    unverified = await runner.attempt(container, "open-an-app", {"ok": True, "verified": False})
    assert unverified.passed is False

    verified = await runner.attempt(container, "open-an-app", {"ok": True, "verified": True})
    assert verified.passed is True


async def test_a_memory_step_needs_both_the_claim_and_the_store(container, record):
    """Checking only the claim believes a reply that said "จำแล้วครับ". Checking only the
    store passes on something written last week. The conjunction is the observation."""
    from thursday_shared.enums import MemoryLayer, MemorySource
    from thursday_shared.models import MemoryWrite

    runner = LessonRunner(record)
    runner.start(container, "remember-this")

    claimed_nothing = await runner.attempt(container, "remember-this", {"reply": "จำแล้วครับ"})
    assert claimed_nothing.passed is False

    invented = await runner.attempt(
        container, "remember-this", {"memory_id": "00000000-0000-0000-0000-000000000009"}
    )
    assert invented.passed is False

    written = await container.memory.write(
        MemoryWrite(
            layer=MemoryLayer.PREFERENCE,
            content="ชอบรายงานแบบสั้น",
            source=MemorySource.USER,
        )
    )
    real = await runner.attempt(container, "remember-this", {"memory_id": written.id})
    assert real.passed is True


async def test_a_step_whose_author_forgot_to_say_how_to_check_it_never_passes():
    """The default `verify` returns False. A step with no observation attached is a step
    that cannot be completed, which is noisier than one that always passes and is the right
    direction for the noise to go."""
    step = lessons.Step(key="x", show="s")
    assert await lessons._run_verify(step, None, {"anything": True}) is False


async def test_a_verify_that_raises_has_not_verified_anything():
    def boom(_c, _e):
        raise RuntimeError("nope")

    step = lessons.Step(key="x", show="s", verify=boom)
    assert await lessons._run_verify(step, None, {}) is False


async def test_every_shipped_step_declares_its_own_check():
    """No lesson relies on the default. This is the test that catches a lesson added later
    with a `show` and a `try_this` and nothing that would notice it failing."""
    default = lessons.Step(key="_", show="_").verify
    for lesson in LESSONS:
        for step in lesson.steps:
            assert step.verify is not default, f"{lesson.id}/{step.key} has no verification"


# ------------------------------------------------------------------------ SHOW → NEXT


async def test_a_completed_step_generalises_rather_than_congratulating(
    container, office_pc, record
):
    """§4's own example ends by widening: "แบบนี้คุณสามารถสั่งเปิดโปรแกรมอื่นได้เช่นกัน".
    A lesson that only says "เรียบร้อย" has taught one command, not a shape."""
    runner = LessonRunner(record)
    runner.start(container, "open-an-app")
    result = await runner.attempt(container, "open-an-app", {"ok": True, "verified": True})
    assert result.passed
    assert "Chrome" in result.message or "โปรแกรมอื่น" in result.message


async def test_a_failed_attempt_shows_the_instruction_again_rather_than_an_error(
    container, office_pc, record
):
    """§27: an error is a teaching moment. The SHOW line is the teaching."""
    runner = LessonRunner(record)
    started = runner.start(container, "open-an-app")
    failed = await runner.attempt(container, "open-an-app", {"ok": False})
    assert failed.message == started.message
    assert leaks(failed.message) == []


async def test_finishing_the_last_step_completes_the_lesson(container, office_pc, record):
    runner = LessonRunner(record)
    runner.start(container, "open-an-app")
    result = await runner.attempt(container, "open-an-app", {"ok": True, "verified": True})
    assert result.done is True
    assert record.progress("open-an-app").status is TutorialStatus.COMPLETED


# --------------------------------------------------------------- what moves familiarity


async def test_starting_a_lesson_only_reaches_discovered(container, record):
    """Sprint 67's rule, enforced from the other side: being shown a lesson is not learning."""
    runner = LessonRunner(record)
    runner.start(container, "say-something")
    assert record.knows("conversation") is Familiarity.DISCOVERED


async def test_completing_a_step_counts_as_a_use_not_an_introduction(container, office_pc, record):
    runner = LessonRunner(record)
    runner.start(container, "open-an-app")
    await runner.attempt(container, "open-an-app", {"ok": True, "verified": True})
    assert record.knows("open_app") is Familiarity.TRIED
    assert record.entry("open_app").uses == 1


async def test_a_failed_attempt_moves_nothing(container, office_pc, record):
    runner = LessonRunner(record)
    runner.start(container, "open-an-app")
    await runner.attempt(container, "open-an-app", {"ok": False})
    assert record.entry("open_app").uses == 0


# ------------------------------------------------------------------------ §2 skipping


def test_skipping_records_a_skip_and_never_a_completion(container, record):
    """§9 keeps completed and skipped in separate lists so nobody can later confuse "they
    passed on this" with "they learned this"."""
    runner = LessonRunner(record)
    runner.start(container, "say-something")
    result = runner.skip("say-something")

    progress = record.progress("say-something")
    assert result.done is True
    assert progress.status is TutorialStatus.SKIPPED
    assert progress.completed_steps == []
    assert progress.skipped_steps == ["talk"]


# ------------------------------------------------------ §12/§52 availability, again


def test_a_lesson_for_hardware_this_machine_lacks_is_not_offered(container, record):
    """The catalogue decides, not the lesson. This lesson is installed and `enabled` and
    still cannot run — which is the whole point of asking the machine."""
    assert runnable(container, LESSONS_BY_ID["open-an-app"]) is False
    assert LESSONS_BY_ID["open-an-app"].enabled is True
    assert "open-an-app" not in {lesson.id for lesson in available(container, record)}


async def test_and_is_offered_the_moment_the_machine_can(container, office_pc, record):
    assert "open-an-app" in {lesson.id for lesson in available(container, record)}


def test_starting_an_unrunnable_lesson_explains_why_instead_of_failing(container, runner):
    result = runner.start(container, "open-an-app")
    assert result.passed is False
    assert "เครื่อง" in result.message
    assert leaks(result.message) == []


def test_the_path_shows_unavailable_lessons_with_their_reason(container, record):
    """Hiding them would mean the owner never learns Thursday could do more with a second
    machine attached — which is a thing they might want to know."""
    rows = [row for stage in path(container, record) for row in stage["lessons"]]
    blocked = [row for row in rows if not row["available"]]
    assert blocked
    for row in blocked:
        assert row["reason"]
        assert leaks(row["reason"]) == []


# --------------------------------------------------------------------- §32/§57 one at a time


def test_next_lesson_returns_one_thing_not_a_list(container, record):
    """§57's "อย่าสอนทุกอย่างวันแรก" is a statement about pacing, and the honest
    implementation of pacing is returning one item."""
    offer = next_lesson(container, record)
    assert offer is not None
    assert isinstance(offer.lesson, lessons.Lesson)


def test_the_first_thing_offered_is_the_shallowest(container, record):
    assert next_lesson(container, record).lesson.id == "say-something"


def test_stopping_is_taught_before_anything_that_touches_the_machine(record, container):
    """§56 wants it early, and the ordering is the substance: knowing how to stop something
    is what makes it safe to try the things that do something."""
    order = [lesson.id for lesson in LESSONS]
    assert order.index("how-to-stop") < order.index("open-an-app")
    assert LESSONS_BY_ID["open-an-app"].after == ("how-to-stop",)


def test_a_lesson_already_learned_is_not_offered_again(container, record):
    for _ in range(3):
        record.used("conversation")
    assert record.knows("conversation") >= Familiarity.LEARNED
    assert "say-something" not in {lesson.id for lesson in available(container, record)}


def test_a_skipped_prerequisite_lowers_a_lesson_rather_than_locking_it(container, record):
    """§58: "Feature ไม่จำเป็นต้องถูกล็อกจริง". Prerequisites sort; they do not gate."""
    record.skip("say-something")
    remaining = {lesson.id for lesson in available(container, record)}
    assert "how-to-stop" in remaining


def test_nothing_is_offered_once_everything_runnable_is_done(container, record):
    for lesson in available(container, record):
        record.complete(lesson.id)
    assert next_lesson(container, record) is None


# ------------------------------------------------------------------------- §3 quick intro


def test_the_quick_intro_is_four_or_five_things_not_the_whole_product():
    """§3: "Thursday สอนเพียงความสามารถพื้นฐาน 4–5 อย่าง … ห้ามสอน Feature ทั้งหมดพร้อมกัน"."""
    assert 4 <= len(QUICK_INTRO) <= 5


def test_the_quick_intro_names_lessons_that_exist():
    """Named by id rather than written out, so the introduction cannot promise a lesson
    that was renamed or removed."""
    for lesson_id in QUICK_INTRO:
        assert lesson_id in LESSONS_BY_ID


def test_the_quick_intro_covers_the_basics_the_spec_lists():
    covered = {LESSONS_BY_ID[i].capability for i in QUICK_INTRO}
    assert {"conversation", "open_app", "file_search", "memory"} <= covered


# --------------------------------------------------------------------------- §16 the path


def test_the_path_never_shows_a_level_number():
    """§16 warns against making it feel like a game, so the game-flavoured spelling does not
    exist here — there is no level integer that could leak into a screen."""
    for stage in Stage:
        assert not stage.value.isdigit()
        assert "LEVEL" not in stage.value.upper() or stage.value == "LEVEL"
    fields = set(inspect.signature(lessons.Lesson).parameters)
    assert "level" not in fields
    assert "points" not in fields
    assert "score" not in fields


def test_every_lesson_is_written_in_the_owners_language():
    for lesson in LESSONS:
        assert lesson.name
        assert leaks(lesson.name) == [], lesson.id
        for step in lesson.steps:
            assert step.show
            assert leaks(step.show) == [], f"{lesson.id}/{step.key}"
            assert leaks(step.then) == [], f"{lesson.id}/{step.key}"
            assert leaks(step.try_this) == [], f"{lesson.id}/{step.key}"


def test_every_lesson_names_a_capability_the_catalogue_knows():
    """The join that makes §52 work. A lesson naming a capability nothing knows about would
    be unrunnable forever and nobody would notice."""
    from thursday_core.catalogue import FEATURES_BY_KEY

    for lesson in LESSONS:
        assert lesson.capability in FEATURES_BY_KEY, lesson.id


def test_lesson_ids_are_unique():
    assert len(LESSONS_BY_ID) == len(LESSONS)


def test_every_prerequisite_names_a_real_lesson():
    for lesson in LESSONS:
        for prior in lesson.after:
            assert prior in LESSONS_BY_ID, f"{lesson.id} -> {prior}"


# ------------------------------------------------------------------------------ §60 offline


def test_no_lesson_reaches_the_network():
    """§60: the tutorial must work with the network down, which is the state a lot of first
    runs are actually in.

    Checked by walking the imports rather than by scanning for method names. The first
    version of this test looked for calls named `get` and failed on `dict.get` — the same
    mistake Sprint 46 made scanning text for "curl" and Sprint 52 made matching its own
    docstring. A module that never imports a client cannot make a request, and that is a
    fact about the module rather than a guess about its spelling.
    """
    import ast
    from pathlib import Path

    tree = ast.parse(Path(lessons.__file__).read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    clients = {"httpx", "requests", "urllib", "aiohttp", "http", "socket", "websockets"}
    assert not (imported & clients), f"lessons.py imports {imported & clients}"
