"""The expression as it actually reaches a screen (Sprint 80).

The unit tests prove the derivation. These prove the two things that only fail in
assembly: that the projector feeds it real events, and that both ways out of the process —
the socket and the endpoint — say the same thing.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from thursday_api.app import create_app
from thursday_core.expression import Mood
from thursday_core.plain import leaks
from thursday_core.world import WorldState, WorldStateProjector
from thursday_realtime.gateway import client_event
from thursday_shared.models import Event


@pytest.fixture
def ws_client(settings, container, office_pc):
    app = create_app(settings, container=container)
    app.state.container = container
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
async def client(settings, container, office_pc):
    app = create_app(settings, container=container)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://thursday.test"
    ) as http:
        app.state.container = container
        yield http


def agent_event(kind: str, **payload) -> Event:
    return Event(kind=kind, payload={"agent": "ResearchAgent", **payload})


# ------------------------------------------------------------------------ the projector


async def test_a_running_agent_puts_its_activity_on_the_world():
    world = WorldState()
    projector = WorldStateProjector(world)

    await projector.on_agent(agent_event("agent.started", activity="กำลังค้นข้อมูล"))

    snapshot = world.snapshot()
    assert snapshot.running_agents == {"ResearchAgent": "working"}
    assert snapshot.current_activity == "กำลังค้นข้อมูล"


async def test_an_agent_that_finished_is_no_longer_running():
    """`running_agents` used to keep every agent that had ever run, forever.

    The field is named for agents that are running, the socket derives "is anything
    happening" from it, and a dict that only grows answers yes for the rest of the session.
    """
    world = WorldState()
    projector = WorldStateProjector(world)

    await projector.on_agent(agent_event("agent.started", activity="กำลังค้นข้อมูล"))
    await projector.on_agent(agent_event("agent.completed", ok=True))

    snapshot = world.snapshot()
    assert snapshot.running_agents == {}
    assert snapshot.current_activity == "", "a finished job left its caption on the screen"
    assert snapshot.last_success_at is not None
    assert snapshot.last_failure_at is None


async def test_a_failure_is_remembered_by_when_it_happened():
    world = WorldState()
    projector = WorldStateProjector(world)

    await projector.on_agent(agent_event("agent.started", activity="กำลังค้นข้อมูล"))
    await projector.on_agent(agent_event("agent.failed", error="เชื่อมต่อไม่ได้"))

    snapshot = world.snapshot()
    assert snapshot.running_agents == {}
    assert snapshot.last_failure_at is not None


async def test_one_agent_finishing_does_not_clear_another_one_still_working():
    world = WorldState()
    projector = WorldStateProjector(world)

    await projector.on_agent(agent_event("agent.started", activity="กำลังค้นข้อมูล"))
    await projector.on_agent(
        Event(
            kind="agent.started",
            payload={"agent": "VisionAgent", "activity": "กำลังวิเคราะห์ภาพ"},
        )
    )
    await projector.on_agent(agent_event("agent.completed", ok=True))

    snapshot = world.snapshot()
    assert snapshot.running_agents == {"VisionAgent": "working"}
    assert snapshot.current_activity == "กำลังวิเคราะห์ภาพ"


# -------------------------------------------------------------------------- the endpoint


async def test_the_endpoint_reports_a_derived_expression(client):
    body = (await client.get("/api/v1/expression")).json()

    assert body["mood"] in {m.value for m in Mood}
    assert 0.0 <= body["intensity"] <= 1.0
    assert body["because"]
    for text in (body["because"], body["activity"]):
        assert not leaks(text), leaks(text)


async def test_the_endpoint_notices_a_stop(client, container):
    container.permissions.set_lockdown(True)
    try:
        assert (await client.get("/api/v1/expression")).json()["mood"] == Mood.STOPPED.value
    finally:
        container.permissions.set_lockdown(False)
    assert (await client.get("/api/v1/expression")).json()["mood"] != Mood.STOPPED.value


# ---------------------------------------------------------------------------- the socket


def test_the_socket_says_how_it_is_going_before_being_asked(ws_client):
    """A client that opens onto a broken machine should not have to wait for a tick."""
    with ws_client.websocket_connect("/api/v1/realtime") as ws:
        assert ws.receive_json()["type"] == "ready"
        opening = ws.receive_json()

    assert opening["type"] == "expression"
    assert opening["mood"] in {m.value for m in Mood}
    assert not leaks(opening["because"]), leaks(opening["because"])


def test_the_socket_says_what_it_is_doing_before_it_answers(ws_client, adapter, office_pc):
    """ "กำลังฟังอยู่" that arrives with the answer is a caption, not a state.

    Renamed in Sprint 85. It was called `test_a_turn_says_it_is_listening_before_it_answers`
    and asserted nothing whatsoever about listening — only that an `expression` frame arrives
    before the first token. The name was a claim the body did not make, which is how
    `Turn.listening` went five sprints with no producer and a green suite. What actually
    covers listening is `test_the_microphone_reaches_the_client` below.
    """
    with ws_client.websocket_connect("/api/v1/realtime") as ws:
        assert ws.receive_json()["type"] == "ready"
        assert ws.receive_json()["type"] == "expression"

        ws.send_json(
            {"type": "turn", "text": "Thursday เปิด chrome", "device_id": str(office_pc.device_id)}
        )

        order: list[str] = []
        for _ in range(40):
            message = ws.receive_json()
            order.append(message["type"])
            if message["type"] == "assistant.delta":
                break

    assert "expression" in order, "the socket never said what it was doing"
    assert order.index("expression") < order.index("assistant.delta")


async def test_a_stop_announces_itself(container):
    """The loudest state Thursday has must not be silent.

    Until Sprint 82 `emergency_stop` published nothing at all. With a task running or a
    device connected the socket noticed anyway, because *those* emit events — but a stop on
    an idle Thursday produced no event, and every open window went on showing a calm
    assistant. Found by pressing the button and watching the avatar keep strolling about.
    """
    seen: list[str] = []

    async def note(event: Event) -> None:
        seen.append(event.kind)

    container.bus.subscribe("system.*", note)

    await container.emergency_stop("all")
    assert "system.emergency_stop" in seen

    await container.release_lockdown()
    assert "system.lockdown_released" in seen

    # And both reach a client rather than being dropped in translation.
    assert client_event("system.emergency_stop") == "notification"
    assert client_event("system.lockdown_released") == "notification"


def test_a_stop_reaches_the_screen_promptly(ws_client):
    """Not "eventually" — the socket re-reads health every twenty seconds regardless, so a
    test that only waits would pass on the fallback and prove nothing. This one is timed:
    the announcement arrives in about a second, and the fallback is twenty."""
    with ws_client.websocket_connect("/api/v1/realtime") as ws:
        assert ws.receive_json()["type"] == "ready"
        assert ws.receive_json()["type"] == "expression"

        started = time.monotonic()
        ws_client.post("/api/v1/emergency/stop", json={"scope": "all"})

        moods = []
        for _ in range(12):
            message = ws.receive_json()
            if message["type"] == "expression":
                moods.append(message["mood"])
                if message["mood"] == Mood.STOPPED.value:
                    break
        elapsed = time.monotonic() - started

        assert Mood.STOPPED.value in moods, moods
        assert elapsed < 5, f"the stop took {elapsed:.1f}s — that is the health tick, not the event"

        ws_client.post("/api/v1/emergency/release")
        for _ in range(12):
            message = ws.receive_json()
            if message["type"] == "expression" and message["mood"] != Mood.STOPPED.value:
                break
        else:
            raise AssertionError("lifting the stop was never announced")


# ------------------------------------------------------------------- the microphone (§10)


class OpenMicrophone:
    """The one property `ExpressionFeed` is allowed to ask the voice loop about.

    Deliberately not a `VoiceService`: what is under test is the wiring between two modules
    that were never joined, and a real service would let a passing test mean the state
    machine happened to be in a listening state rather than that the feed read it.
    """

    def __init__(self, listening: bool) -> None:
        self.listening = listening


def test_the_microphone_reaches_the_client(ws_client, container):
    """The regression test for the defect Sprint 85 opened with.

    `Turn.listening` was read by `express()` from Sprint 80 onward and set by nothing at all,
    while `VoiceService.listening` sat live on the container documented as "the one a UI must
    trust". Every recording indicator drawn from an expression frame was therefore dark, on
    every platform, in every state. Nothing failed; the field was simply never true.
    """
    container.voice = OpenMicrophone(True)

    with ws_client.websocket_connect("/api/v1/realtime") as ws:
        assert ws.receive_json()["type"] == "ready"
        opening = ws.receive_json()

    assert opening["type"] == "expression"
    assert opening["listening"] is True, "the microphone was open and the client was not told"
    assert opening["posture"] == "LISTENING"


def test_a_closed_microphone_reaches_the_client_too(ws_client, container):
    """The other half, so the test above cannot pass by hard-coding true."""
    container.voice = OpenMicrophone(False)

    with ws_client.websocket_connect("/api/v1/realtime") as ws:
        assert ws.receive_json()["type"] == "ready"
        opening = ws.receive_json()

    assert opening["listening"] is False
    assert opening["posture"] != "LISTENING"


def test_starting_a_turn_does_not_put_the_microphone_out(ws_client, container, adapter, office_pc):
    """Why the microphone is read at send time rather than carried on `feed.turn`.

    The receive loop replaces `feed.turn` wholesale at four points, and each replacement is
    a chance to drop a flag carried on it — the first of them firing the instant a turn
    begins, which is exactly when the microphone is most likely to be open. Reading it in
    `payload()` makes that structurally impossible; this is the test that would notice if
    somebody moved it back.
    """
    container.voice = OpenMicrophone(True)

    with ws_client.websocket_connect("/api/v1/realtime") as ws:
        assert ws.receive_json()["type"] == "ready"
        assert ws.receive_json()["type"] == "expression"

        ws.send_json(
            {"type": "turn", "text": "Thursday เปิด chrome", "device_id": str(office_pc.device_id)}
        )

        expressions = []
        for _ in range(40):
            message = ws.receive_json()
            if message["type"] == "expression":
                expressions.append(message)
            if message["type"] == "assistant.delta":
                break

    assert expressions, "the socket said nothing about what it was doing"
    assert all(frame["listening"] is True for frame in expressions), (
        "a turn beginning switched the recording indicator off"
    )


def test_the_socket_and_the_endpoint_report_the_same_microphone(ws_client, container):
    """The claim `/expression` makes in its own docstring, tested rather than asserted.

    "Both call `express`, so there is one place that decides what Thursday is feeling and no
    way for the two to disagree" was true when it was written and would have quietly stopped
    being true the moment §10's field was added — the socket knows about the voice loop and
    an endpoint that only passes a world snapshot does not. Same machine, same second,
    different answer about whether the microphone is on.
    """
    container.voice = OpenMicrophone(True)

    over_http = ws_client.get("/api/v1/expression").json()
    with ws_client.websocket_connect("/api/v1/realtime") as ws:
        assert ws.receive_json()["type"] == "ready"
        over_socket = ws.receive_json()

    assert over_http["listening"] is True
    assert over_socket["listening"] is over_http["listening"]
    assert over_socket["posture"] == over_http["posture"]


# ------------------------------------------------------ §13, from the agent to the screen


async def test_a_running_agent_puts_a_prop_on_the_world_beside_its_phrase():
    world = WorldState()
    projector = WorldStateProjector(world)

    await projector.on_agent(agent_event("agent.started", activity="กำลังค้นข้อมูล", prop="BOOKS"))

    snapshot = world.snapshot()
    assert snapshot.current_prop == "BOOKS"
    assert snapshot.current_activity == "กำลังค้นข้อมูล"


async def test_the_prop_is_put_down_with_the_phrase():
    """One clear, not two — the pair that describes work has to end together."""
    world = WorldState()
    projector = WorldStateProjector(world)

    await projector.on_agent(agent_event("agent.started", activity="กำลังเขียนโค้ด", prop="CODE"))
    await projector.on_agent(agent_event("agent.completed", ok=True))

    snapshot = world.snapshot()
    assert snapshot.current_prop == ""
    assert snapshot.current_activity == ""


async def test_a_real_agent_emits_the_prop_its_capabilities_earn():
    """The whole path, from what an agent *is* to what the robot holds.

    Not a hand-written payload: this runs a real `BaseAgent` and captures what it actually
    emits, so the capability → prop table is exercised by the code that will use it. A prop
    table nothing emits into is the shape of the defect this project keeps finding — and
    `agent` being in the same payload is the reason the assertion below is not merely "a
    prop arrived" but "the prop came from the capability, and the name did not come at all".
    """
    from thursday_agents.base import BaseAgent
    from thursday_shared.ids import new_id
    from thursday_shared.models import AgentResult, AgentSpec, JobContract, Spend

    emitted: list[Event] = []

    class Ctx:
        spend = Spend()

        async def emit(self, event: Event) -> None:
            emitted.append(event)

    class Researcher(BaseAgent):
        spec = AgentSpec(
            name="ResearchAgent",
            description="finds things",
            capabilities=["research", "search"],
        )

        async def execute(self, contract, ctx):
            return AgentResult(agent="ResearchAgent", ok=True, output={"found": 1})

    contract = JobContract(
        task_id=new_id(),
        step_id=new_id(),
        agent="research",
        objective="find the budget file",
        output_schema={"found": "int"},
        success_criteria=["output.found is present"],
    )
    result = await Researcher().run(contract=contract, ctx=Ctx())  # type: ignore[arg-type]
    assert result.ok, result.error

    started = next(e for e in emitted if e.kind == "agent.started")
    assert started.payload["prop"] == "BOOKS"
    assert started.payload["activity"] == "กำลังค้นข้อมูล"
    # The name is present for the projector and the log, and is not a rendered field —
    # `graph.test.ts` and `AgentStrip.test.tsx` hold the other end of that rule.
    assert started.payload["agent"] == "ResearchAgent"
