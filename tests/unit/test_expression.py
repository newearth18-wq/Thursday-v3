"""Thursday's own expression (Sprint 80).

The two claims worth testing are not "does it return a mood" but the two the module's
docstring makes about what it *cannot* do: a mood cannot be asserted by a caller, and a
mood cannot be built from anything about the person. Both are structural, so both are
tested structurally — by walking the module rather than by calling it.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from thursday_core import expression as expression_module
from thursday_core.expression import (
    BECAUSE,
    DROWSY,
    FRESH,
    ORDER,
    POSTURE_ORDER,
    SURE_ENOUGH,
    Expression,
    Mood,
    Posture,
    Turn,
    express,
)
from thursday_core.plain import ACTIVITY_BY_CAPABILITY, WORKING, leaks
from thursday_shared.ids import new_id
from thursday_shared.models import WorldStateSnapshot

SOURCE = ast.parse(Path(inspect.getfile(expression_module)).read_text(encoding="utf-8"))


def world(**fields) -> WorldStateSnapshot:
    return WorldStateSnapshot(**fields)


# --------------------------------------------------------------------- priority, not a blend


def test_every_mood_is_ranked():
    """`ORDER` is the whole ranking, so a new mood cannot arrive without a position."""
    assert set(ORDER) == set(Mood)
    assert len(ORDER) == len(Mood)
    assert set(BECAUSE) == set(Mood), "a mood with no sentence would render as nothing"


def test_a_stop_outranks_everything_pleasant():
    """Three cheerful signals must not average away the loudest state Thursday has."""
    now = datetime.now(UTC)
    happy = world(last_success_at=now, running_agents={"a": "working"}, current_activity="x")
    assert express(happy, unhealthy=0, lockdown=False).mood is Mood.WORKING
    assert express(happy, unhealthy=0, lockdown=True).mood is Mood.STOPPED


def test_a_failure_outranks_work_still_running():
    now = datetime.now(UTC)
    busy_and_broken = world(
        running_agents={"a": "working"}, current_activity="กำลังค้นข้อมูล", last_failure_at=now
    )
    assert express(busy_and_broken, unhealthy=0, lockdown=False).mood is Mood.FAILING


def test_something_that_needs_the_owner_outranks_something_that_looks_nice():
    now = datetime.now(UTC)
    waiting = world(pending_approvals=[new_id()], last_success_at=now)
    assert express(waiting, unhealthy=0, lockdown=False).mood is Mood.WAITING


@pytest.mark.parametrize(
    ("kwargs", "turn", "expected"),
    [
        ({"lockdown": True, "unhealthy": 0}, Turn(), Mood.STOPPED),
        ({"lockdown": False, "unhealthy": 1}, Turn(), Mood.CONCERNED),
        ({"lockdown": False, "unhealthy": 0}, Turn(verified=False), Mood.UNSURE),
        ({"lockdown": False, "unhealthy": 0}, Turn(confidence=0.2), Mood.UNSURE),
        ({"lockdown": False, "unhealthy": 0}, Turn(thinking=True), Mood.ATTENTIVE),
        ({"lockdown": False, "unhealthy": 0}, Turn(listening=True), Mood.ATTENTIVE),
        ({"lockdown": False, "unhealthy": 0}, Turn(speaking=True), Mood.ATTENTIVE),
        ({"lockdown": False, "unhealthy": 0}, Turn(), Mood.CALM),
    ],
)
def test_each_mood_is_reachable(kwargs, turn, expected):
    assert express(world(), turn=turn, **kwargs).mood is expected


def test_confidence_just_under_the_bar_is_not_confident():
    """The threshold is the one the conversation view already uses, not a second opinion."""
    below = Turn(confidence=SURE_ENOUGH - 0.01)
    at = Turn(confidence=SURE_ENOUGH)
    assert express(world(), unhealthy=0, lockdown=False, turn=below).mood is Mood.UNSURE
    assert express(world(), unhealthy=0, lockdown=False, turn=at).mood is Mood.CALM


# ------------------------------------------------------------------------------ it fades


def test_a_failure_stops_colouring_the_mood_once_it_is_old():
    """Thursday is sorry about a failure, and then it stops being sorry.

    Before Sprint 80 the projector left a finished agent in `running_agents` marked
    "failed" forever, so a mood built on it would have been a permanent apology.
    """
    now = datetime.now(UTC)
    fresh = world(last_failure_at=now - FRESH + timedelta(seconds=1))
    stale = world(last_failure_at=now - FRESH - timedelta(seconds=1))
    assert express(fresh, unhealthy=0, lockdown=False, now=now).mood is Mood.FAILING
    assert express(stale, unhealthy=0, lockdown=False, now=now).mood is Mood.CALM


def test_a_success_stops_colouring_the_mood_once_it_is_old():
    now = datetime.now(UTC)
    stale = world(last_success_at=now - FRESH - timedelta(seconds=1))
    assert express(stale, unhealthy=0, lockdown=False, now=now).mood is Mood.CALM


# ----------------------------------------------------------------- what reaches the screen


def test_the_activity_line_is_empty_when_nothing_is_running():
    """A leftover phrase under a finished job reads as work still in progress."""
    assert express(world(current_activity="กำลังค้นข้อมูล"), unhealthy=0, lockdown=False).activity == ""


def test_the_activity_line_is_the_allowlisted_phrase():
    running = world(running_agents={"a": "working"}, current_activity="กำลังค้นข้อมูล")
    assert express(running, unhealthy=0, lockdown=False).activity in ACTIVITY_BY_CAPABILITY.values()


def test_an_unnamed_activity_becomes_the_vague_phrase_not_an_empty_gap():
    """Sprint 65's fallback, carried through: vague and always true beats blank."""
    running = world(running_agents={"a": "working"}, current_activity="")
    assert express(running, unhealthy=0, lockdown=False).activity == WORKING


def test_nothing_a_screen_renders_leaks_an_internal():
    now = datetime.now(UTC)
    for spot in (
        world(running_agents={"ResearchAgent": "working"}, current_activity="กำลังค้นข้อมูล"),
        world(last_failure_at=now),
        world(pending_approvals=[new_id()]),
    ):
        shown = express(spot, unhealthy=1, lockdown=False)
        for text in (shown.activity, shown.because):
            assert not leaks(text), leaks(text)
        assert "Agent" not in shown.activity


def test_intensity_stays_inside_the_range_however_much_is_happening():
    """It drives an animation; a value above 1 is a component drawn off the screen."""
    busy = world(
        running_agents={f"a{i}": "working" for i in range(30)},
        pending_approvals=[new_id() for _ in range(30)],
        current_activity="กำลังค้นข้อมูล",
    )
    for spot, health, stopped in ((busy, 20, False), (world(), 0, True), (world(), 0, False)):
        shown = express(spot, unhealthy=health, lockdown=stopped)
        assert 0.0 <= shown.intensity <= 1.0


# ------------------------------------------------------------- a mood cannot be asserted


def test_a_caller_cannot_state_the_mood():
    """ADR 0012's rule, applied to a feeling: no parameter through which to assert one.

    A mood that can be assigned is a mood that will read CALM while the audit chain is
    broken, because the code that assigns it is not the code that noticed.
    """
    assert dataclasses.is_dataclass(Expression) and Expression.__dataclass_fields__
    assert Expression.__dataclass_params__.frozen, "an expression could be edited after the fact"

    for node in ast.walk(SOURCE):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            args = node.args
            names = {a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
            assert "mood" not in names, f"{node.name}() takes a mood from its caller"


def test_the_two_inputs_that_are_not_on_the_snapshot_are_required():
    """A default of zero is how a calm face ends up on a machine with a dead database."""
    with pytest.raises(TypeError):
        express(world())  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        express(world(), unhealthy=0)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        express(world(), lockdown=False)  # type: ignore[call-arg]


def test_the_stop_is_read_from_where_it_is_actually_set():
    """`WorldStateSnapshot.lockdown` is declared and never written by anything.

    A mood built on it would have been permanently false while claiming to show the
    loudest state Thursday has, so the parameter exists and the field is not read.
    """
    assert express(world(lockdown=True), unhealthy=0, lockdown=False).mood is not Mood.STOPPED
    for node in ast.walk(SOURCE):
        if isinstance(node, ast.Attribute) and node.attr == "lockdown":
            assert not (isinstance(node.value, ast.Name) and node.value.id == "world"), (
                "the dead field is being read again"
            )


# ------------------------------------------------- §55: this is never a reading of a person

#: Packages that hold something about a person — a face, a voice, a template, a camera
#: frame. §55 forbids inferring emotion or anything else from any of them, and the way to
#: keep that true is for this module to have no path to them at all.
PERSON_BEARING = ("thursday_security", "thursday_vision", "thursday_voice", "thursday_devices")


def test_the_expression_cannot_reach_anything_about_a_person():
    imported: list[str] = []
    for node in ast.walk(SOURCE):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    for module in imported:
        assert not module.startswith(PERSON_BEARING), (
            f"{module} carries observations of a person; a mood must not be able to read one"
        )


def test_no_input_describes_the_person():
    """The type signature is the guarantee: there is nowhere for a face to go."""
    fields = {f.name for f in dataclasses.fields(Turn)}
    assert fields == {"thinking", "speaking", "listening", "verified", "confidence"}


# ------------------------------------------------------------------ the second axis (§7)


def test_posture_and_mood_answer_different_questions():
    """A failure forty seconds old must not be able to hide a live microphone.

    This is the whole argument for two tables rather than one longer one. Flattened onto a
    single priority order, `FAILING` outranks everything, and the owner watching the avatar
    would see the failure while the recording indicator §10 requires quietly vanished.
    """
    just_failed = world(last_failure_at=datetime.now(UTC))
    now = express(just_failed, unhealthy=0, lockdown=False, turn=Turn(listening=True))

    assert now.mood is Mood.FAILING, "the failure is still the news"
    assert now.posture is Posture.LISTENING, "and the body is still listening"
    assert now.listening is True


@pytest.mark.parametrize(
    "mood_setup",
    [
        {"lockdown": True},
        {"unhealthy": 3},
        {"world": {"last_failure_at": "now"}},
        {"world": {"last_success_at": "now"}},
        {"turn": {"verified": False}},
        {},
    ],
)
def test_no_mood_can_switch_the_microphone_off(mood_setup):
    """§10, structurally. `listening` sits outside both tables and answers to nobody.

    Parametrised over the states that outrank almost everything else, because the failure
    mode is not "the flag is wrong" — it is "the flag is right until something more urgent
    happens", which is precisely when a person most wants to know the microphone is open.
    """
    setup = {"unhealthy": 0, "lockdown": False}
    setup.update({k: v for k, v in mood_setup.items() if k not in ("world", "turn")})
    fields = {
        key: datetime.now(UTC) if value == "now" else value
        for key, value in mood_setup.get("world", {}).items()
    }
    turn = Turn(listening=True, **mood_setup.get("turn", {}))

    seen: set[Mood] = set()
    for waiting in ([], [new_id()]):
        result = express(world(pending_approvals=waiting, **fields), turn=turn, **setup)
        seen.add(result.mood)
        assert result.listening is True, f"{result.mood} switched off the microphone"
        assert result.payload()["listening"] is True
    assert seen, "no mood was exercised at all"


def test_barge_in_shows_the_speaking_and_keeps_the_microphone_lit():
    """The one case where the posture is outranked and the boolean is not.

    During barge-in the microphone is open *while* Thursday is still talking (V4). The body
    should show the speaking — that is what the owner is interrupting — but the microphone
    is still capturing, and nothing about the pose is allowed to say otherwise.
    """
    both = express(world(), unhealthy=0, lockdown=False, turn=Turn(speaking=True, listening=True))
    assert both.posture is Posture.SPEAKING
    assert both.listening is True


def test_every_posture_is_reachable():
    """A member nothing can produce is a claim, not a state — the defect this sprint found.

    Each posture is asserted from the inputs a running Thursday actually supplies, so a
    member added without a signal behind it has nowhere to come from and fails here.
    """
    long_ago = datetime.now(UTC) - DROWSY - timedelta(seconds=1)
    reached = {
        express(world(), unhealthy=0, lockdown=False, turn=turn).posture
        for turn in (Turn(speaking=True), Turn(thinking=True), Turn(listening=True), Turn())
    }
    reached.add(
        express(world(running_agents={"a": "working"}), unhealthy=0, lockdown=False).posture
    )
    reached.add(express(world(last_event_at=long_ago), unhealthy=0, lockdown=False).posture)

    assert reached == set(Posture), f"unreachable: {set(Posture) - reached}"


def test_posture_order_covers_every_posture_exactly_once():
    assert set(POSTURE_ORDER) == set(Posture)
    assert len(POSTURE_ORDER) == len(Posture)


def test_authenticating_is_not_a_posture_until_something_can_produce_one():
    """§19 deferred on purpose, and asserted absent so it cannot be half-added.

    `IdentityGate` and `AuthenticationSession` exist in `thursday_security`, but nothing
    constructs them on the container — there is no live "verification in flight" signal in a
    running Thursday. Drawing the face anyway would be this project's oldest bug: a state
    that is documented, rendered, and permanently false. When the identity layer is wired,
    this test is what tells whoever wires it that the avatar is waiting for the signal.
    """
    assert not hasattr(Posture, "AUTHENTICATING")


# ------------------------------------------------------------------------- sleep (§20)


def test_thursday_sleeps_only_after_real_quiet():
    quiet = datetime.now(UTC) - DROWSY - timedelta(seconds=1)
    recent = datetime.now(UTC) - timedelta(seconds=30)

    assert (
        express(world(last_event_at=quiet), unhealthy=0, lockdown=False).posture is Posture.SLEEPING
    )
    assert (
        express(world(last_event_at=recent), unhealthy=0, lockdown=False).posture is Posture.STILL
    )


def test_a_thursday_that_has_never_seen_an_event_is_awake():
    """`None` is "just started", not "asleep since the beginning of time".

    The two mistakes are not equal. Looking awake while idle is unremarkable; looking asleep
    on a machine that is in fact working is a lie about what the owner's computer is doing.
    """
    assert express(world(), unhealthy=0, lockdown=False).posture is Posture.STILL


def test_a_question_waiting_on_the_owner_never_falls_asleep():
    """Standing in front of somebody waiting for an answer is not a reason to doze off."""
    long_ago = datetime.now(UTC) - DROWSY - timedelta(days=1)
    asked = express(
        world(last_event_at=long_ago, pending_approvals=[new_id()]),
        unhealthy=0,
        lockdown=False,
    )
    assert asked.posture is Posture.STILL
    assert asked.mood is Mood.WAITING


def test_the_microphone_check_actually_reaches_every_mood():
    """The guard above is only worth as much as the moods it walks through.

    Written after the parametrised case above passed a deliberately broken build: its cases
    happened to miss `FAILING`, which is the mood most likely to be given a vote, because a
    recent failure is exactly the signal somebody would think should dominate. A test that
    covers every state but the dangerous one is the shape of a false negative.
    """
    covered: set[Mood] = set()
    for setup, fields, turn in (
        ({"lockdown": True, "unhealthy": 0}, {}, Turn(listening=True)),
        ({"lockdown": False, "unhealthy": 2}, {}, Turn(listening=True)),
        (
            {"lockdown": False, "unhealthy": 0},
            {"last_failure_at": datetime.now(UTC)},
            Turn(listening=True),
        ),
        (
            {"lockdown": False, "unhealthy": 0},
            {"last_success_at": datetime.now(UTC)},
            Turn(listening=True),
        ),
        ({"lockdown": False, "unhealthy": 0}, {}, Turn(listening=True, verified=False)),
        (
            {"lockdown": False, "unhealthy": 0},
            {"running_agents": {"a": "working"}},
            Turn(listening=True),
        ),
        (
            {"lockdown": False, "unhealthy": 0},
            {"pending_approvals": [new_id()]},
            Turn(listening=True),
        ),
        ({"lockdown": False, "unhealthy": 0}, {}, Turn(listening=True)),
    ):
        result = express(world(**fields), turn=turn, **setup)
        covered.add(result.mood)
        assert result.listening is True, f"{result.mood} switched off the microphone"

    assert covered == set(Mood) - {Mood.CALM}, (
        "every mood but CALM, which an open microphone rules out by definition"
    )
