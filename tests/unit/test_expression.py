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
    FRESH,
    ORDER,
    SURE_ENOUGH,
    Expression,
    Mood,
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
