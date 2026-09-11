"""Wake-on-LAN (ADDENDUM §20) — Sprint 60.

§20's sequence is short and every step is load-bearing:

    request approval/policy check → wake device → wait for Node online → route task

**Sending is not succeeding.** A magic packet is a UDP broadcast. Nothing acknowledges it,
nothing routes it back, and a machine that is unplugged, has WoL disabled in firmware, or sits
on a different subnet swallows it in exactly the same silence as one that is booting. So
`wake` cannot report success from having sent — that would be the `verified: true` lie the
whole project is built to prevent (ADR 0012).

The only honest evidence a machine woke is **the node connecting**. That is an observation
the core already makes, so waking is an ACT → VERIFY pair like every other device action: send
the packet, then wait for the hub to register that device. No node, no success.

**Waking is a physical act.** It draws power, spins fans, lights a room, and may be happening
at three in the morning next to somebody asleep. That is why §20 says "request approval/policy
check" rather than "wake": the Permission Engine decides, and this module refuses to be a
second door (§95). Nothing here checks a policy — it is called *after* one, and the endpoint
that calls it is where the engine runs.

**A MAC address is configuration, not discovery.** It is recorded when the owner sets it, not
sniffed from the network: Thursday learning MAC addresses by watching traffic would be the
same reconnaissance ADR 0044 refused for inference endpoints, and a magic packet sent to an
address Thursday guessed is a packet sent at somebody else's machine.
"""

from __future__ import annotations

import asyncio
import re
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from thursday_core.logging import get_logger

log = get_logger(__name__)

#: The standard magic packet: six 0xFF bytes, then the MAC repeated sixteen times.
_SYNC = b"\xff" * 6
_REPEATS = 16

#: Port 9 (discard) is the conventional destination. 7 (echo) also works on some hardware;
#: both are sent because a NIC that listens on one and not the other is common enough, and a
#: second UDP datagram costs nothing.
_PORTS = (9, 7)

#: How long to wait for the machine to appear before calling the wake failed. Generous: a
#: desktop with a spinning disk and a slow POST can take most of a minute before its node
#: dials home, and declaring failure early would have Thursday report "it did not wake" about
#: a machine that is, at that moment, waking.
DEFAULT_TIMEOUT_S = 90.0

#: How often to look for the node. Cheap — it is a dictionary lookup, not a probe.
POLL_S = 1.0

_MAC = re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")


class InvalidMac(ValueError):
    """Not a MAC address. Refused before anything is sent."""


@dataclass(frozen=True)
class WakeResult:
    """What happened, in the shape every device action reports.

    `sent` and `verified` are separate fields because they are separate facts, and conflating
    them is the whole trap: the packet always sends, and the machine sometimes wakes.
    """

    device_id: UUID
    sent: bool
    verified: bool
    waited_s: float = 0.0
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.verified


def magic_packet(mac: str) -> bytes:
    """The bytes for one MAC. Validated here so a malformed address cannot become a
    broadcast of arbitrary content."""
    if not _MAC.match(mac.strip()):
        raise InvalidMac(f"{mac!r} is not a MAC address")
    raw = bytes.fromhex(mac.replace(":", "").replace("-", ""))
    return _SYNC + raw * _REPEATS


class WakeOnLan:
    """Sends the packet and waits for the machine to prove it woke."""

    def __init__(
        self,
        hub: Any,
        *,
        broadcast: str = "255.255.255.255",
        timeout_s: float = DEFAULT_TIMEOUT_S,
        send: Any = None,
    ) -> None:
        self._hub = hub
        self._broadcast = broadcast
        self._timeout_s = timeout_s
        #: Injected so a test can watch what would go on the wire without one leaving it.
        #: A test that broadcasts real magic packets from CI is a test that wakes somebody's
        #: machine.
        self._send = send or _broadcast_packet

    async def wake(
        self, device_id: UUID, mac: str, *, timeout_s: float | None = None
    ) -> WakeResult:
        """Wake a machine, and report whether it actually woke.

        Called *after* the Permission Engine has authorised it. This is not the gate.
        """
        try:
            packet = magic_packet(mac)
        except InvalidMac as exc:
            return WakeResult(device_id, sent=False, verified=False, error=str(exc))

        if self._hub.summary(device_id) is not None and self._online(device_id):
            # Already awake. Sending anyway would be harmless and reporting a wake would not
            # be: "I woke it" and "it was already on" are different answers to the owner.
            return WakeResult(device_id, sent=False, verified=True, error="already online")

        try:
            self._send(packet, self._broadcast, _PORTS)
        except OSError as exc:
            log.warning("wake_send_failed", device_id=str(device_id), error=str(exc))
            return WakeResult(device_id, sent=False, verified=False, error=str(exc))

        log.info("wake_sent", device_id=str(device_id), broadcast=self._broadcast)
        waited = await self._await_node(device_id, timeout_s or self._timeout_s)

        if waited is None:
            # §38 — not silent, and not optimistic. The packet went out and the machine did
            # not appear; those are both true and only the second one matters to the caller.
            return WakeResult(
                device_id,
                sent=True,
                verified=False,
                waited_s=timeout_s or self._timeout_s,
                error="the packet was sent and the machine did not come online",
            )

        log.info("wake_verified", device_id=str(device_id), after_s=round(waited, 1))
        return WakeResult(device_id, sent=True, verified=True, waited_s=waited)

    async def _await_node(self, device_id: UUID, timeout_s: float) -> float | None:
        """Wait for the node to connect. Returns how long it took, or None if it never did.

        Polls the hub rather than subscribing to an event, deliberately: the hub is the thing
        that knows, a dictionary lookup once a second costs nothing, and an event subscription
        would need unsubscribing on every exit path — including the timeout, which is the one
        somebody forgets.
        """
        started = asyncio.get_running_loop().time()
        deadline = started + timeout_s
        while asyncio.get_running_loop().time() < deadline:
            if self._online(device_id):
                return asyncio.get_running_loop().time() - started
            await asyncio.sleep(POLL_S)
        return None

    def _online(self, device_id: UUID) -> bool:
        summary = self._hub.summary(device_id)
        if summary is None:
            return False
        from thursday_shared.enums import DeviceStatus

        return summary.status is DeviceStatus.ONLINE


def _broadcast_packet(packet: bytes, broadcast: str, ports: tuple[int, ...]) -> None:
    """One socket, both ports. Broadcast is enabled explicitly because it is off by default
    and a silent EACCES would look exactly like a machine that did not wake."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for port in ports:
            sock.sendto(packet, (broadcast, port))


@dataclass
class WakeRecord:
    """What Thursday knows about waking one machine (§20's optional capability).

    The MAC is stored per device and is set by the owner. `enabled` is separate from having
    an address: recording where a machine is and being willing to wake it are two decisions,
    and an owner who turns waking off should not have to delete the address to do it.
    """

    device_id: UUID
    mac: str
    enabled: bool = False
    broadcast: str = "255.255.255.255"
    last_woken_at: datetime | None = None

    def row(self) -> dict:
        return {
            "device_id": str(self.device_id),
            "mac": self.mac,
            "enabled": self.enabled,
            "broadcast": self.broadcast,
            "last_woken_at": self.last_woken_at.isoformat() if self.last_woken_at else None,
        }

    @classmethod
    def from_row(cls, row: dict) -> WakeRecord:
        stamp = row.get("last_woken_at")
        return cls(
            device_id=UUID(str(row["device_id"])),
            mac=str(row["mac"]),
            enabled=bool(row.get("enabled", False)),
            broadcast=str(row.get("broadcast") or "255.255.255.255"),
            last_woken_at=datetime.fromisoformat(stamp).replace(tzinfo=UTC) if stamp else None,
        )
