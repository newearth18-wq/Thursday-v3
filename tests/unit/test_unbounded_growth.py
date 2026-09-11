"""Collections on long-lived objects that used to grow for the life of the process.

Sprint 86. Found by walking every class for `self.x.append(...)` / `.add(...)` with no
matching pop, clear or slice anywhere in the class. The interesting thing about this defect
is that nothing ever fails: the process simply gets larger, forever, and a desktop assistant
meant to be left running for weeks is exactly where that stops being theoretical.

`BenchmarkProfile.samples` was already a `deque(maxlen=...)` with a comment explaining why
— so the pattern was known here, and these were the places it had not been applied.
"""

from __future__ import annotations

from thursday_core.bus import DEDUPE_LIMIT, InProcessEventBus
from thursday_shared.models import Event
from thursday_voice.state import HISTORY_LIMIT, VoiceState, VoiceStateMachine

# ------------------------------------------------------------------------------ the bus


async def test_the_replay_guard_does_not_remember_every_event_forever():
    """`_seen` was an unbounded set on the hottest path in the system.

    Every event Thursday publishes added an id that was never removed. `_history`, declared
    two lines above it in the same `__init__`, was bounded from the start — which is what
    makes this a slip rather than a decision.
    """
    bus = InProcessEventBus(dedupe_limit=64)

    for _ in range(1_000):
        await bus.publish(Event(kind="test.tick"))

    assert len(bus._seen) <= 64, "the replay guard grew without bound"
    assert len(bus._history) <= 500


async def test_the_replay_guard_still_refuses_a_replay_inside_its_window():
    """Bounding it must not stop it doing its job."""
    seen: list[Event] = []
    bus = InProcessEventBus(dedupe_limit=64)
    bus.subscribe("*", seen.append)

    event = Event(kind="test.once")
    await bus.publish(event)
    await bus.publish(event)
    await bus.publish(event)

    assert len(seen) == 1, "a replay inside the window was delivered again"


async def test_a_replay_older_than_the_window_is_delivered_again():
    """Stated rather than hidden: this is the cost of bounding it, and it is the documented
    at-least-once contract. Handlers are required to be idempotent, and the guard has always
    been a courtesy on top of that rather than a promise."""
    seen: list[Event] = []
    bus = InProcessEventBus(dedupe_limit=4)
    bus.subscribe("*", seen.append)

    old = Event(kind="test.old")
    await bus.publish(old)
    for _ in range(10):
        await bus.publish(Event(kind="test.filler"))
    await bus.publish(old)

    assert len(seen) == 12
    assert DEDUPE_LIMIT > 500, "the shipped window must be far larger than this test's"


# ------------------------------------------------------------------------------ voice


def test_the_voice_machine_does_not_remember_every_transition_forever():
    """One append per wake, per utterance, per barge-in — on an object that lives as long
    as Thursday does, to serve a debugging aid nobody reads past the last few entries."""
    machine = VoiceStateMachine()

    for _ in range(HISTORY_LIMIT * 3):
        machine.to(VoiceState.LISTENING)
        machine.reset()

    assert len(machine.history) <= HISTORY_LIMIT
    # And it kept the *recent* end, which is the end anybody ever looks at.
    assert machine.history[-1] == (VoiceState.LISTENING, VoiceState.IDLE)


def test_the_voice_machine_still_reports_what_just_happened():
    machine = VoiceStateMachine()
    machine.to(VoiceState.LISTENING)
    machine.to(VoiceState.CAPTURING)

    assert [step[1] for step in machine.history] == [
        VoiceState.LISTENING,
        VoiceState.CAPTURING,
    ]
