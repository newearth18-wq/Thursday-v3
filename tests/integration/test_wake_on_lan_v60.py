"""Wake-on-LAN (ADDENDUM §20) — Sprint 60.

§20's sequence: request approval/policy check → wake device → wait for Node online → route
task. Two of those four steps are where this could go wrong.

**"Sent" is not "woke".** A magic packet is a UDP broadcast. Nothing acknowledges it, and a
machine that is unplugged, has WoL disabled in firmware, or sits behind a router swallows it
in exactly the same silence as one that is booting. Reporting success from having sent would
be the `verified: true` lie the whole project exists to prevent, so the only evidence accepted
here is the node connecting.

**Waking is a physical act.** It draws power, spins fans, and may be happening beside somebody
asleep. §20 says "request approval/policy check" rather than "wake", and the policy is
ASK_ALWAYS to begin with for the same reason §102 and §104 give for their categories.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from thursday_api.app import create_app
from thursday_devices import actions as catalogue
from thursday_devices.wake import InvalidMac, WakeOnLan, WakeRecord, magic_packet
from thursday_security.policy import PolicyTable
from thursday_shared.enums import DeviceStatus, PolicyDecision
from thursday_shared.ids import new_id

GPU_PC = new_id()
MAC = "AA:BB:CC:DD:EE:FF"


class Hub:
    """A hub whose device can be made to appear, the way a booting machine appears."""

    def __init__(self, online: bool = False) -> None:
        self._online = online

    def summary(self, device_id):
        if not self._online:
            return None
        return type("Summary", (), {"status": DeviceStatus.ONLINE})()

    def arrives(self) -> None:
        self._online = True


class Wire:
    """Records what would have gone on the wire. Nothing leaves the machine.

    A test that broadcasts real magic packets from CI is a test that wakes somebody's
    computer, which is a rude way to find out the code works.
    """

    def __init__(self, fails: bool = False) -> None:
        self.packets: list[tuple[bytes, str, tuple[int, ...]]] = []
        self.fails = fails

    def __call__(self, packet: bytes, broadcast: str, ports: tuple[int, ...]) -> None:
        if self.fails:
            raise OSError("broadcast is not permitted on this socket")
        self.packets.append((packet, broadcast, ports))


# --------------------------------------------------------------------------- the packet


def test_a_magic_packet_is_six_ff_bytes_and_the_mac_sixteen_times():
    packet = magic_packet(MAC)
    assert len(packet) == 102
    assert packet[:6] == b"\xff" * 6
    assert packet[6:12] == bytes.fromhex("aabbccddeeff")
    assert packet[6:] == bytes.fromhex("aabbccddeeff") * 16


@pytest.mark.parametrize("mac", ["AA:BB:CC:DD:EE:FF", "aa-bb-cc-dd-ee-ff", "aabbccddeeff".upper()])
def test_the_usual_ways_of_writing_a_mac_are_accepted_or_refused_consistently(mac):
    if mac.count(":") == 5 or mac.count("-") == 5:
        assert len(magic_packet(mac)) == 102
    else:
        # No separators is not a MAC by this parser, and refusing is better than guessing:
        # a malformed address becomes a broadcast of arbitrary bytes.
        with pytest.raises(InvalidMac):
            magic_packet(mac)


@pytest.mark.parametrize(
    "bad", ["not-a-mac", "AA:BB:CC:DD:EE", "AA:BB:CC:DD:EE:FF:00", "ZZ:BB:CC:DD:EE:FF", ""]
)
def test_a_malformed_address_is_refused_before_anything_is_sent(bad):
    with pytest.raises(InvalidMac):
        magic_packet(bad)


# --------------------------------------------------------------------------- sent vs woke


async def test_a_machine_that_comes_online_is_reported_as_woken():
    hub, wire = Hub(), Wire()
    waker = WakeOnLan(hub, send=wire, timeout_s=5.0)

    async def boots():
        await asyncio.sleep(0.05)
        hub.arrives()

    task = asyncio.create_task(boots())
    result = await waker.wake(GPU_PC, MAC)
    await task

    assert result.sent is True
    assert result.verified is True
    assert wire.packets, "no packet was sent"


async def test_a_machine_that_never_appears_is_not_reported_as_woken():
    """The property this sprint is about. The packet always sends; the machine sometimes
    wakes; reporting the first as the second is the lie ADR 0012 exists to prevent."""
    wire = Wire()
    waker = WakeOnLan(Hub(), send=wire, timeout_s=0.2)

    result = await waker.wake(GPU_PC, MAC)

    assert result.sent is True, "the packet did go out"
    assert result.verified is False, "and nothing woke"
    assert result.ok is False
    assert "did not come online" in result.error


async def test_the_packet_goes_to_both_conventional_ports():
    """Port 9 is conventional and 7 works on hardware that ignores 9. A second datagram
    costs nothing and saves a class of "it just does not work on that NIC"."""
    wire = Wire()
    await WakeOnLan(Hub(), send=wire, timeout_s=0.1).wake(GPU_PC, MAC)
    assert wire.packets[0][2] == (9, 7)


async def test_a_machine_that_is_already_awake_is_not_claimed_as_woken():
    """ "I woke it" and "it was already on" are different answers, and only one of them is
    true."""
    wire = Wire()
    result = await WakeOnLan(Hub(online=True), send=wire).wake(GPU_PC, MAC)

    assert result.verified is True
    assert result.sent is False
    assert result.error == "already online"
    assert wire.packets == [], "nothing needed sending"


async def test_a_socket_that_refuses_to_broadcast_is_reported_not_swallowed():
    """Broadcast is off by default on a UDP socket, and a silent EACCES looks exactly like a
    machine that would not wake — which is the wrong thing to tell the owner."""
    result = await WakeOnLan(Hub(), send=Wire(fails=True), timeout_s=0.1).wake(GPU_PC, MAC)

    assert result.sent is False
    assert result.verified is False
    assert "broadcast is not permitted" in result.error


async def test_a_bad_address_never_reaches_the_wire():
    wire = Wire()
    result = await WakeOnLan(Hub(), send=wire).wake(GPU_PC, "nonsense")
    assert result.sent is False and result.verified is False
    assert wire.packets == []


# --------------------------------------------------------------------------- the policy


def test_waking_asks_every_time_to_begin_with():
    """§20 says "request approval/policy check". A new capability with a physical
    consequence starts by asking, like §102's external communication and §104's delete."""
    policy = PolicyTable().get("device.wake")
    assert policy.default is PolicyDecision.ASK_ALWAYS


def test_the_action_declares_that_it_must_be_verified():
    """The catalogue is where "this action needs an observation" is recorded, and waking
    needs one more than most: its only failure mode is silence."""
    spec = catalogue.get("device.wake")
    assert spec is not None
    assert spec.verify is True
    assert spec.reversible is False


# --------------------------------------------------------------------------- through the app


@pytest.fixture
def client(settings, container):
    app = create_app(settings, container=container)
    app.state.container = container
    with TestClient(app) as http:
        yield http


def test_a_device_with_no_recorded_address_cannot_be_woken(client):
    """Thursday does not guess. An address it invented is a packet aimed at somebody
    else's machine."""
    assert client.post(f"/api/v1/devices/{GPU_PC}/wake").status_code == 404


def test_recording_an_address_does_not_by_itself_permit_waking(client, container):
    """Two decisions, taken separately: where the machine is, and whether Thursday may wake
    it. An owner who says the first has not said the second."""
    stored = client.put(f"/api/v1/devices/{GPU_PC}/wake-on-lan", params={"mac": MAC}).json()
    assert stored["enabled"] is False

    refused = client.post(f"/api/v1/devices/{GPU_PC}/wake")
    assert refused.status_code == 409
    assert "disabled" in refused.json()["detail"]


def test_a_malformed_address_is_refused_at_the_endpoint(client):
    refused = client.put(f"/api/v1/devices/{GPU_PC}/wake-on-lan", params={"mac": "nope"})
    assert refused.status_code == 422


def test_waking_goes_through_the_permission_engine(client, container):
    """§95 — there is no second door. With the default ASK_ALWAYS policy and no approval,
    the request is refused before a packet is built."""
    sent: list = []
    container.wake_records[GPU_PC] = WakeRecord(device_id=GPU_PC, mac=MAC, enabled=True)
    container.wake._send = lambda *a: sent.append(a)

    refused = client.post(f"/api/v1/devices/{GPU_PC}/wake")

    assert refused.status_code == 403
    assert refused.json()["detail"]["decision"] == PolicyDecision.ASK_ALWAYS.value
    assert sent == [], "a packet was sent despite the refusal"
