"""The workflow builder's HTTP surface, against the real application (V15).

`/automations` was in the API's rate-limit list from the start and had no routes under it:
the engine ran, rules existed, and nothing outside the process could see or change one.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from thursday_api.app import create_app

BASE = "/api/v1/automations"


@pytest.fixture
async def client(settings, container):
    app = create_app(settings, container=container)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000"
    ) as http:
        app.state.container = container
        yield http


def graph(**overrides) -> dict:
    return {
        "name": "สรุปเช้าวันทำงาน",
        "nodes": [
            {
                "id": "t",
                "kind": "trigger",
                "subkind": "schedule",
                "config": {"cron": "30 7 * * 1-5"},
            },
            {"id": "a", "kind": "action", "subkind": "notify", "config": {"title": "สรุปงานวันนี้"}},
        ],
        "order": ["a"],
        **overrides,
    }


# ------------------------------------------------------------------------ preview


async def test_preview_validates_without_writing_anything(client):
    before = (await client.get(BASE)).json()["count"]
    response = await client.post(f"{BASE}/preview", json=graph())
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["problems"] == []
    assert body["explanation"].startswith("07:30")
    assert (await client.get(BASE)).json()["count"] == before


async def test_preview_names_the_node_a_problem_is_in(client):
    broken = graph(
        nodes=[
            {"id": "t", "kind": "trigger", "subkind": "schedule", "config": {"cron": "ทุกเช้า"}},
            {"id": "a", "kind": "action", "subkind": "notify", "config": {"title": "x"}},
        ]
    )
    body = (await client.post(f"{BASE}/preview", json=broken)).json()
    assert body["valid"] is False
    assert any(p["node"] == "t" for p in body["problems"])


async def test_preview_shows_what_each_action_would_be_allowed_to_do(client):
    with_tool = graph(
        nodes=[
            {"id": "t", "kind": "trigger", "subkind": "manual", "config": {}},
            {"id": "a", "kind": "action", "subkind": "tool", "config": {"name": "file.delete"}},
        ]
    )
    body = (await client.post(f"{BASE}/preview", json=with_tool)).json()
    (consequence,) = body["consequences"]
    assert consequence["action"] == "file.delete"
    assert consequence["decision"] == "ASK_ALWAYS"
    assert consequence["blocked"] is False


async def test_a_blocked_verb_is_visible_on_the_canvas_not_at_three_in_the_morning(client):
    blocked = graph(
        nodes=[
            {"id": "t", "kind": "trigger", "subkind": "manual", "config": {}},
            {
                "id": "a",
                "kind": "action",
                "subkind": "tool",
                "config": {"name": "security.disable"},
            },
        ]
    )
    body = (await client.post(f"{BASE}/preview", json=blocked)).json()
    assert body["consequences"][0]["blocked"] is True


# ------------------------------------------------------------------------- saving


async def test_a_saved_rule_is_never_armed_by_saving_it(client):
    """Save and arm are different decisions, so they are different calls."""
    created = await client.post(BASE, json={**graph(), "enabled": True})
    assert created.status_code == 200
    assert created.json()["enabled"] is False

    listed = (await client.get(BASE)).json()["automations"]
    mine = next(a for a in listed if a["name"] == "สรุปเช้าวันทำงาน")
    assert mine["enabled"] is False


async def test_a_graph_that_cannot_run_is_refused_rather_than_stored(client):
    response = await client.post(BASE, json=graph(name="   "))
    assert response.status_code == 422
    assert "ชื่อ" in response.json()["detail"]


async def test_enabling_is_its_own_call(client):
    rule_id = (await client.post(BASE, json=graph())).json()["id"]
    enabled = await client.post(f"{BASE}/{rule_id}/enable")
    assert enabled.json()["enabled"] is True
    off = await client.post(f"{BASE}/{rule_id}/enable?enabled=false")
    assert off.json()["enabled"] is False


async def test_editing_a_rule_keeps_the_owners_decision_about_whether_it_runs(client):
    """Re-saving must not silently disarm a live rule, nor arm a parked one."""
    rule_id = (await client.post(BASE, json=graph())).json()["id"]
    await client.post(f"{BASE}/{rule_id}/enable")

    edited = await client.put(f"{BASE}/{rule_id}", json=graph(name="สรุปเช้า (แก้ไข)"))
    assert edited.status_code == 200
    assert edited.json()["enabled"] is True

    listed = (await client.get(BASE)).json()["automations"]
    assert any(a["name"] == "สรุปเช้า (แก้ไข)" for a in listed)
    assert not any(a["name"] == "สรุปเช้าวันทำงาน" for a in listed)


async def test_an_edit_does_not_multiply_the_rule(client):
    rule_id = (await client.post(BASE, json=graph())).json()["id"]
    before = (await client.get(BASE)).json()["count"]
    await client.put(f"{BASE}/{rule_id}", json=graph(name="เปลี่ยนชื่อ"))
    assert (await client.get(BASE)).json()["count"] == before


async def test_an_unknown_rule_is_a_404_not_a_new_one(client):
    missing = "00000000-0000-4000-8000-000000000000"
    assert (await client.put(f"{BASE}/{missing}", json=graph())).status_code == 404
    assert (await client.post(f"{BASE}/{missing}/enable")).status_code == 404
    assert (await client.delete(f"{BASE}/{missing}")).status_code == 404


async def test_deleting_removes_it(client):
    rule_id = (await client.post(BASE, json=graph())).json()["id"]
    assert (await client.delete(f"{BASE}/{rule_id}")).json()["removed"] is True
    assert not any(
        a["automation_id"] == rule_id for a in (await client.get(BASE)).json()["automations"]
    )


# -------------------------------------------------------------------- running it


async def test_a_rule_can_be_tried_before_it_is_armed(client):
    """Trying it before arming it is the point of the button, so it works while disabled."""
    rule_id = (await client.post(BASE, json=graph())).json()["id"]
    response = await client.post(f"{BASE}/{rule_id}/run")
    assert response.status_code == 200
    assert response.json()["ran"] is True and response.json()["steps"] == 1


# ------------------------------------------------------------------- the catalogue


async def test_the_catalogue_says_which_triggers_have_no_runner(client):
    """Served rather than hardcoded in the client, so the menu cannot drift from the truth."""
    body = (await client.get(f"{BASE}/catalogue")).json()
    by_kind = {t["kind"]: t for t in body["triggers"]}
    assert by_kind["schedule"]["unavailable"] == ""
    assert by_kind["event"]["unavailable"] == ""
    assert "ยังไม่มีตัวเฝ้าดู" in by_kind["state_change"]["unavailable"]


async def test_the_catalogue_carries_every_tools_permission_facts(client):
    body = (await client.get(f"{BASE}/catalogue")).json()
    assert body["tools"], "the container has tools registered"
    for tool in body["tools"]:
        assert tool["level"] in {"READ", "OPEN", "MODIFY", "EXTERNAL", "SYSTEM", "ADMIN"}
        assert tool["decision"] in {"AUTO", "ASK_ONCE", "ASK_ALWAYS", "BLOCK"}


async def test_a_schedule_is_read_back_in_words_as_the_owner_types(client):
    good = (await client.get(f"{BASE}/schedule/describe", params={"cron": "30 7 * * 1-5"})).json()
    assert good["valid"] is True and good["reads_as"] == "07:30 วันจันทร์ถึงศุกร์"

    bad = (await client.get(f"{BASE}/schedule/describe", params={"cron": "ทุกเช้า"})).json()
    assert bad["valid"] is False and bad["reads_as"] == "" and bad["problem"]


# ------------------------------------------------------------ rules Thursday wrote


async def test_a_rule_thursday_suggested_opens_on_the_canvas_too(client, container):
    """Otherwise the owner has two places rules live: one they can see and one they cannot."""
    from thursday_automation.rules import Action, Automation, Trigger

    container.automations.add(
        Automation(
            name="กิจวัตรตอนเย็น",
            trigger=Trigger(kind="schedule", cron="0 17 * * *"),
            actions=[Action(kind="notify", args={"title": "สรุปเย็น"})],
            created_by="thursday_suggested",
        )
    )
    listed = (await client.get(BASE)).json()["automations"]
    suggested = next(a for a in listed if a["name"] == "กิจวัตรตอนเย็น")
    assert suggested["created_by"] == "thursday_suggested"
    assert suggested["enabled"] is False
    assert suggested["explanation"].startswith("17:00")
    assert [n["kind"] for n in suggested["nodes"]] == ["trigger", "action"]
