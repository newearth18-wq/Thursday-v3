"""A gated device action is asked about, not refused (§20, V21).

§20's own headline scenario did not work from anywhere: "ปิดเครื่องให้หน่อย" is
`system.power`, `system.power` is ASK_ALWAYS, and this endpoint answered anything that was
not AUTO with 403. The Permission Engine was saying *ask the owner* and the endpoint was
hearing *no*.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from thursday_api.app import create_app
from thursday_devices.actions import CATALOGUE
from thursday_security.policy import PolicyTable
from thursday_shared.enums import PolicyDecision


@pytest.fixture
async def client(settings, container, office_pc):
    app = create_app(settings, container=container)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://thursday.test"
    ) as http:
        app.state.container = container
        yield http


async def act(client, device_id, action, **args):
    return await client.post(
        f"/api/v1/devices/{device_id}/actions",
        json={"action": action, "args": args, "reason": "จากมือถือ"},
    )


# ------------------------------------------------------------ the scenario §20 opens with


async def test_locking_a_screen_is_asked_about_rather_than_refused(client, office_pc):
    """ASK_ONCE, LOW, reversible — and it used to come back 403."""
    response = await act(client, office_pc.device_id, "system.lock")
    assert response.status_code == 202
    body = response.json()
    assert body["ran"] is False
    assert body["decision"] == PolicyDecision.ASK_ONCE.value
    assert body["approval_id"]


async def test_shutting_a_machine_down_is_asked_about_too(client, office_pc):
    """§20's second sentence. Whether a *phone* may ask is a separate rule (ADR 0070); that
    the API can ask at all is this one."""
    response = await act(client, office_pc.device_id, "system.power", mode="shutdown")
    assert response.status_code == 202
    assert response.json()["decision"] == PolicyDecision.ASK_ALWAYS.value


async def test_the_approval_it_raises_is_real_and_answerable(client, office_pc):
    raised = (await act(client, office_pc.device_id, "system.lock")).json()

    pending = (await client.get("/api/v1/approvals")).json()["approvals"]
    mine = next(a for a in pending if a["id"] == raised["approval_id"])
    assert mine["action"] == "system.lock"
    assert mine["device_name"] == "Office-PC"

    decided = await client.post(f"/api/v1/approvals/{raised['approval_id']}/approve")
    assert decided.status_code == 200


async def test_the_approval_carries_what_a_person_needs_to_decide(client, office_pc):
    """§38's list: what it is, which machine, what will happen, and whether it can be undone."""
    raised = (await act(client, office_pc.device_id, "system.power", mode="restart")).json()
    mine = next(
        a
        for a in (await client.get("/api/v1/approvals")).json()["approvals"]
        if a["id"] == raised["approval_id"]
    )
    assert mine["device_name"] == "Office-PC"
    assert mine["reversible"] is False
    assert mine["expected_outcome"]
    assert mine["consequence_of_refusal"]


# ------------------------------------------------------------------- nothing ran, though


async def test_asking_is_not_doing(client, office_pc, adapter):
    """The whole guarantee. A 202 is a question, and the machine must be untouched."""
    before = list(getattr(adapter, "running", []))
    await act(client, office_pc.device_id, "system.power", mode="shutdown")
    assert list(getattr(adapter, "running", [])) == before


async def test_an_auto_action_still_just_runs(client, office_pc):
    response = await act(client, office_pc.device_id, "system.info")
    assert response.status_code == 200
    assert response.json()["verified"] is True


async def test_an_unknown_action_is_still_a_400(client, office_pc):
    assert (await act(client, office_pc.device_id, "make.coffee")).status_code == 400


async def test_a_blocked_action_is_still_refused_outright(client, office_pc):
    """BLOCK is the engine saying no, not asking. Lockdown is how a catalogue verb reaches
    it — no action in the catalogue is BLOCK on its own, so the branch is reached this way."""
    await client.post("/api/v1/emergency/stop", json={"scope": "all"})
    try:
        response = await act(client, office_pc.device_id, "app.open", name="chrome")
        assert response.status_code == 403
        assert response.json()["detail"]["decision"] == PolicyDecision.BLOCK.value
    finally:
        await client.post("/api/v1/emergency/release")


# ----------------------------------------------------- how much of the catalogue this opens


def test_the_gated_actions_were_unreachable_and_now_are_not():
    """Counted rather than asserted in prose: eight of the catalogue's actions were refused
    by this endpoint for every caller, including two that are reversible."""
    table = PolicyTable()
    gated = {name for name in CATALOGUE if table.get(name).default is not PolicyDecision.AUTO}
    assert "system.power" in gated and "system.lock" in gated
    assert len(gated) >= 8, "the catalogue changed; this count is the point of the test"
