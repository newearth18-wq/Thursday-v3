"""The expression as it actually reaches a screen (Sprint 80).

The unit tests prove the derivation. These prove the two things that only fail in
assembly: that the projector feeds it real events, and that both ways out of the process —
the socket and the endpoint — say the same thing.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from thursday_api.app import create_app
from thursday_core.expression import Mood
from thursday_core.plain import leaks
from thursday_core.world import WorldState, WorldStateProjector
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


def test_a_turn_says_it_is_listening_before_it_answers(ws_client, adapter, office_pc):
    """ "กำลังฟังอยู่" that arrives with the answer is a caption, not a state."""
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
