"""The HTTP surface, exercised against the real application."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from thursday.api.app import create_app
from thursday.shared.enums import PolicyDecision


@pytest.fixture
async def client(settings, container, office_pc):
    app = create_app(settings, container=container)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://thursday.test"
    ) as http:
        # The lifespan hook is not run by ASGITransport, so attach the container directly.
        app.state.container = container
        yield http


async def test_health_reports_every_component(client):
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    components = {check["component"] for check in response.json()["checks"]}
    assert "devices" in components
    assert "audit" in components
    assert any(c.startswith("model:") for c in components)


async def test_a_conversation_turn_returns_a_verified_reply(client, adapter):
    response = await client.post("/api/v1/conversation", json={"text": "Thursday เปิด chrome"})
    assert response.status_code == 200
    body = response.json()
    assert body["verified"] is True
    assert body["voice_mode"] == "SUCCESS"
    assert body["trace_id"]
    assert "chrome" in adapter.running


async def test_the_trace_id_survives_the_round_trip(client):
    response = await client.post(
        "/api/v1/conversation", json={"text": "hello"}, headers={"x-trace-id": "abc123"}
    )
    assert response.headers["x-trace-id"] == "abc123"


async def test_conversation_continues_within_a_session(client):
    first = (await client.post("/api/v1/conversation", json={"text": "สวัสดี"})).json()
    session_id = first["session_id"]
    await client.post("/api/v1/conversation", json={"text": "สถานะงาน", "session_id": session_id})

    history = (await client.get(f"/api/v1/conversation/{session_id}")).json()
    assert len(history["turns"]) >= 4  # two owner turns, two replies


async def test_direct_device_control_still_passes_the_permission_engine(
    client, office_pc, tmp_path
):
    """There is no back door around the Permission Engine, not even for the API."""
    allowed = await client.post(
        f"/api/v1/devices/{office_pc.device_id}/action",
        json={"action": "system_info", "args": {}},
    )
    assert allowed.status_code == 200 and allowed.json()["verified"] is True

    refused = await client.post(
        f"/api/v1/devices/{office_pc.device_id}/action",
        json={"action": "run_shell", "args": {"command": "rm -rf /"}},
    )
    assert refused.status_code == 403
    assert refused.json()["detail"]["decision"] == PolicyDecision.ASK.value


async def test_an_unknown_device_is_a_404(client):
    from thursday.shared.ids import new_id

    response = await client.post(
        f"/api/v1/devices/{new_id()}/action", json={"action": "system_info"}
    )
    assert response.status_code == 404


async def test_memory_write_reports_an_honest_refusal(client):
    """The write policy declining is reported, not silently swallowed."""
    accepted = await client.post(
        "/api/v1/memory",
        json={"content": "from now on, always send reports as PDF", "layer": "preference"},
    )
    assert accepted.json()["written"] is True

    declined = await client.post("/api/v1/memory", json={"content": "ok", "layer": "semantic"})
    assert declined.json() == {"written": False, "reason": "small talk"}


async def test_memory_search_returns_scored_records_without_embeddings(client):
    await client.post(
        "/api/v1/memory",
        json={
            "content": "โครงการ Alpha ใช้ Postgres และ pgvector",
            "layer": "semantic",
            "importance": 0.8,
        },
    )
    body = (await client.get("/api/v1/memory/search", params={"q": "Alpha", "k": 3})).json()
    assert body["memories"]
    assert "embedding" not in body["memories"][0]
    assert body["memories"][0]["score"] is not None


async def test_the_audit_endpoint_exposes_the_chain_state(client):
    await client.post("/api/v1/conversation", json={"text": "Thursday เปิด chrome"})
    body = (await client.get("/api/v1/audit")).json()
    assert body["chain_intact"] is True
    assert any(entry["tool"] == "open_app" for entry in body["entries"])


async def test_emergency_stop_locks_down_and_can_be_released(client, office_pc):
    stopped = (await client.post("/api/v1/emergency/stop", json={"scope": "all"})).json()
    assert stopped["actions"]["lockdown"] is True
    assert stopped["actions"]["devices_disconnected"] >= 1

    # While locked down, even an ordinary action is refused.
    refused = await client.post(
        f"/api/v1/devices/{office_pc.device_id}/action",
        json={"action": "open_app", "args": {"name": "chrome"}},
    )
    assert refused.status_code == 403

    assert (await client.post("/api/v1/emergency/release")).json()["lockdown"] is False


async def test_tasks_can_be_listed_and_cancelled(client):
    created = (await client.post("/api/v1/tasks", json={"objective": "a long job"})).json()
    listing = (await client.get("/api/v1/tasks")).json()
    assert listing["count"] >= 1

    cancelled = (await client.post(f"/api/v1/tasks/{created['id']}/cancel")).json()
    assert cancelled["status"] == "CANCELLED"


async def test_the_world_endpoint_reflects_the_connected_device(client, office_pc):
    body = (await client.get("/api/v1/world")).json()
    assert body["active_device_name"] == "Office-PC"


async def test_tools_and_agents_are_introspectable(client):
    tools = (await client.get("/api/v1/tools")).json()["tools"]
    assert {"open_app", "memory_search", "obsidian_write"} <= {t["name"] for t in tools}

    agents = (await client.get("/api/v1/agents")).json()["agents"]
    assert {"computer", "research"} <= {a["name"] for a in agents}


async def test_a_risky_skill_cannot_be_activated_without_review(client):
    """§51, §96 — a learned workflow may not start deleting on Thursday's authority."""
    created = (
        await client.post(
            "/api/v1/skills",
            json={
                "name": "tidy downloads",
                "description": "clear the downloads folder every Friday",
                "steps": [{"tool": "delete", "args": {"path": "~/Downloads/old"}}],
            },
        )
    ).json()
    assert created["status"] == "draft"
    assert created["needs_approval"] is True
    assert created["risky_steps"] == ["delete"]

    tested = (await client.post(f"/api/v1/skills/{created['id']}/test")).json()
    assert tested["ok"] is True

    refused = await client.post(f"/api/v1/skills/{created['id']}/activate")
    assert refused.status_code == 403

    await client.post(f"/api/v1/skills/{created['id']}/approve", params={"approved_by": "owner"})
    activated = (await client.post(f"/api/v1/skills/{created['id']}/activate")).json()
    assert activated["status"] == "active"


async def test_a_harmless_skill_activates_after_its_tests(client):
    created = (
        await client.post(
            "/api/v1/skills",
            json={"name": "morning glance", "steps": [{"tool": "clock"}]},
        )
    ).json()
    assert created["needs_approval"] is False

    await client.post(f"/api/v1/skills/{created['id']}/test")
    activated = (await client.post(f"/api/v1/skills/{created['id']}/activate")).json()
    assert activated["status"] == "active"


async def test_a_skill_can_be_rolled_back(client):
    created = (
        await client.post("/api/v1/skills", json={"name": "s", "steps": [{"tool": "clock"}]})
    ).json()
    await client.post(f"/api/v1/skills/{created['id']}/test")
    await client.post(f"/api/v1/skills/{created['id']}/activate")

    rolled = (
        await client.post(f"/api/v1/skills/{created['id']}/rollback", params={"to": 1})
    ).json()
    assert rolled["current_version"] == 1

    missing = await client.post(f"/api/v1/skills/{created['id']}/rollback", params={"to": 9})
    assert missing.status_code == 404


async def test_routine_suggestions_are_accepted_but_stay_disabled(client, container):
    """§49 — accepting a suggestion records it; enabling it is a separate act."""
    from datetime import UTC, datetime, timedelta

    from thursday.shared.models import Event

    base = datetime.now(UTC).replace(hour=8, minute=15)
    for day in range(4):
        for tool in ("open_app", "open_url", "list_dir"):
            await container.routines.on_tool(
                Event(
                    kind="tool.executed",
                    payload={"tool": tool},
                    occurred_at=base - timedelta(days=day),
                )
            )

    suggestions = (await client.get("/api/v1/routines/suggestions")).json()["suggestions"]
    assert suggestions and "Routine" in suggestions[0]["prompt"]

    accepted = (
        await client.post("/api/v1/routines/suggestions/accept", params={"index": 0})
    ).json()
    assert accepted["enabled"] is False

    listed = (await client.get("/api/v1/automations")).json()["automations"]
    assert listed[0]["created_by"] == "thursday_suggested"

    from uuid import UUID

    enabled = (await client.post(f"/api/v1/automations/{UUID(accepted['id'])}/enable")).json()
    assert enabled["enabled"] is True
