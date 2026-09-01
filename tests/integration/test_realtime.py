"""The realtime channel (PART 72)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from thursday_api.app import create_app
from thursday_realtime.gateway import client_event


@pytest.fixture
def ws_client(settings, container, office_pc):
    app = create_app(settings, container=container)
    app.state.container = container
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize(
    ("internal", "expected"),
    [
        ("task.completed", "task.updated"),
        ("task.failed", "task.updated"),
        ("agent.started", "agent.updated"),
        ("approval.required", "approval.required"),
        ("approval.granted", "approval.resolved"),
        ("device.connected", "device.updated"),
        ("memory.conflict", "notification"),
        ("automation.triggered", "notification"),
    ],
)
def test_internal_events_translate_to_the_client_vocabulary(internal, expected):
    """The UI codes against a small stable set, not against the core's internal topics."""
    assert client_event(internal) == expected


@pytest.mark.parametrize(
    "internal", ["tool.executed", "conversation.turn.received", "memory.created"]
)
def test_internal_chatter_is_not_pushed_to_clients(internal):
    """Sub-agent and tool traffic is Thursday's business, not the owner's (PART 1.1)."""
    assert client_event(internal) is None


def test_a_turn_streams_a_delta_and_a_speech_directive(ws_client, adapter, office_pc):
    with ws_client.websocket_connect("/api/v1/realtime") as ws:
        assert ws.receive_json()["type"] == "ready"

        ws.send_json(
            {"type": "turn", "text": "Thursday เปิด chrome", "device_id": str(office_pc.device_id)}
        )

        # Task and device events stream while the turn is still executing, so the reply
        # arrives after a burst of them rather than first.
        seen: dict[str, dict] = {}
        for _ in range(40):
            message = ws.receive_json()
            seen.setdefault(message["type"], message)
            if "assistant.audio" in seen and "assistant.delta" in seen:
                break

        delta = seen["assistant.delta"]
        assert delta["final"] is True
        assert delta["verified"] is True
        assert "chrome" in delta["text"]
        assert delta["voice_mode"] == "SUCCESS"

        # The directive, not the waveform: audio is synthesised at the edge.
        audio = seen["assistant.audio"]
        assert audio["voice_mode"] == "SUCCESS"
        assert "text" in audio and "voice" in audio

        assert "chrome" in adapter.running


def test_task_updates_reach_the_client(ws_client, office_pc):
    with ws_client.websocket_connect("/api/v1/realtime") as ws:
        ws.receive_json()
        ws.send_json(
            {"type": "turn", "text": "Thursday เปิด chrome", "device_id": str(office_pc.device_id)}
        )

        kinds = set()
        for _ in range(40):
            kinds.add(ws.receive_json()["type"])
            if "task.updated" in kinds:
                break
        assert "task.updated" in kinds


def test_ping_is_answered(ws_client):
    with ws_client.websocket_connect("/api/v1/realtime") as ws:
        ws.receive_json()
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"


def test_interrupt_bypasses_the_planner(ws_client, container, office_pc):
    """PART 98 — stop is the highest-priority command and does not queue behind planning."""
    with ws_client.websocket_connect("/api/v1/realtime") as ws:
        ws.receive_json()
        ws.send_json({"type": "interrupt"})

        for _ in range(10):
            message = ws.receive_json()
            if message["type"] == "assistant.delta":
                assert "หยุด" in message["text"] or "Stopped" in message["text"]
                return
        pytest.fail("no reply to the interrupt")
