"""The deterministic intent rules, and the bus they publish over.

These rules run before any model call, so they decide what a request *is* for free and with
the network down. That makes them worth pinning: a rule that quietly widens is a rule that
turns a question into an action.
"""

from __future__ import annotations

import pytest
from thursday_core import intent_rules
from thursday_core.bus import InProcessEventBus
from thursday_shared.enums import IntentKind
from thursday_shared.models import Event


def kind_of(text: str) -> IntentKind | None:
    match = intent_rules.parse(text)
    return match.intent.kind if match else None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Thursday เปิด chrome", IntentKind.COMPUTER_ACTION),
        ("Thursday close chrome", IntentKind.COMPUTER_ACTION),
        ("Thursday หยุด", IntentKind.STOP),
        ("สถานะงานเป็นยังไง", IntentKind.STATUS),
        ("จำได้ไหมว่าเมื่อวานทำอะไร", IntentKind.MEMORY_RECALL),
        ("จำไว้นะ ผมชอบกาแฟดำ", IntentKind.MEMORY_WRITE),
        ("remember that the dean prefers PDF", IntentKind.MEMORY_WRITE),
    ],
)
def test_the_common_sentences_are_placed_without_a_model_call(text, expected):
    assert kind_of(text) is expected


def test_remembering_and_being_asked_to_recall_are_not_confused():
    """The two are one syllable apart: จำไว้ stores, จำได้ไหม asks. Opposite effects."""
    assert kind_of("จำไว้ว่าห้องทำงานผมคือ 402") is IntentKind.MEMORY_WRITE
    assert kind_of("จำได้ไหมว่าห้องทำงานผมอยู่ไหน") is IntentKind.MEMORY_RECALL
    assert kind_of("do you remember the report?") is IntentKind.MEMORY_RECALL


def test_a_statement_about_the_owner_goes_to_the_preference_layer():
    """And one about somebody else does not — "the dean prefers PDF" is a fact, not a
    standing instruction about how to treat the owner."""
    mine = intent_rules.parse("remember I prefer PDF")
    theirs = intent_rules.parse("remember that the dean prefers PDF")
    assert mine is not None and mine.intent.entities["layer"] == "preference"
    assert theirs is not None and theirs.intent.entities["layer"] == "semantic"


def test_a_bare_verb_is_not_treated_as_a_fact_to_store():
    assert kind_of("remember") is not IntentKind.MEMORY_WRITE


@pytest.mark.parametrize(
    "text",
    [
        "Thursday run shell command whoami",
        "Thursday run cmd /c del *.docx",
        "Thursday run powershell Remove-Item C:\\",
    ],
)
def test_a_shell_instruction_is_never_narrowed_into_an_app_launch(text):
    """§96. Reading "run shell command whoami" as an application named "shell command
    whoami" is not merely wrong, it is wrong in the direction of acting anyway."""
    assert intent_rules.parse(text) is None


@pytest.mark.parametrize("text", ["Thursday open terminal", "Thursday run bash"])
def test_but_a_terminal_is_still_an_application(text):
    assert kind_of(text) is IntentKind.COMPUTER_ACTION


# ------------------------------------------------------------------ the event bus


async def test_a_synchronous_subscriber_is_delivered_to_like_any_other():
    """Appending to a list is the most natural subscriber anyone will write.

    Before this was handled, such a handler did not merely fail — it raised out of
    ``publish`` before any subscriber ran, aborting the task that published the event.
    """
    bus = InProcessEventBus()
    seen: list[str] = []
    bus.subscribe("*", lambda event: seen.append(event.kind))

    await bus.publish(Event(kind="task.completed"))
    assert seen == ["task.completed"]


async def test_one_broken_subscriber_does_not_stop_the_others():
    bus = InProcessEventBus()
    delivered: list[str] = []

    def explodes(event: Event) -> None:
        raise RuntimeError("as expected")

    async def records(event: Event) -> None:
        delivered.append(event.kind)

    bus.subscribe("*", explodes)
    bus.subscribe("*", records)

    await bus.publish(Event(kind="task.failed"))
    assert delivered == ["task.failed"]


async def test_a_replayed_event_is_delivered_once():
    """At-least-once delivery upstream means a duplicate will arrive eventually."""
    bus = InProcessEventBus()
    count: list[int] = []
    bus.subscribe("*", lambda _: count.append(1))

    event = Event(kind="device.connected")
    await bus.publish(event)
    await bus.publish(event)
    assert sum(count) == 1
