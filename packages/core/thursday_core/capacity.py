"""How much of Thursday's own work each machine is carrying (ADDENDUM §129) — Sprint 102.

§23's release notes listed "distributed stages run sequentially" as a gap waiting on a
per-device concurrency limit that did not exist. This is that limit, and building it needed
one correction to the reason it was wanted.

**The router's idea of "busy" comes from the machine, and arrives too late to bind.**
`ComputeLoad` — `gpu_percent`, `queue_depth` — is telemetry a node reports with its
heartbeat. It describes the machine a moment ago. Dispatch three stages in the same
millisecond and all three are routed against the same pre-dispatch snapshot, so all three
land on whichever machine looked idle. Measured against the shipped router with two equally
idle machines, three concurrent `choose()` calls returned the same machine three times.

So the count that binds cannot be the one the machine reports. It has to be the one Thursday
keeps for itself: **how many jobs this process has dispatched to that machine and not yet
had back.** That number is exact, it is current, and it exists before any heartbeat could
carry it.

Two mechanisms, because they answer different questions:

* `in_flight` is a **preference**. The router prefers a machine carrying less of Thursday's
  work, ahead of the telemetry, because a count of what we just sent is strictly better
  information than a snapshot of what the machine last said. It is advisory: two stages can
  read it before either has taken a slot, and then both prefer the same machine.
* `hold` is the **guarantee**. A machine runs at most `limit` of Thursday's jobs at once,
  and a stage that would exceed that waits for a slot. Racing the preference costs a worse
  spread; it cannot cost saturation.

**The default limit is one, and that is not timidity.** A second heavy inference on the same
GPU does not finish sooner — it shares VRAM with the first, and on a card sized for one
model it fails outright. The gain from running stages at once is *across* machines, which is
what §21's example describes. A machine that can genuinely serve more says so through
`set_limit`; nothing on a machine reports its own answer, so there is no better default to
infer.

**The cloud is never limited.** A provider runs its own concurrency and its own queue, and a
cap here would be Thursday inventing a bound nobody asked for on hardware it does not own.
Rate and spend are already governed at the router (§45); this is about not flattening a
machine in the owner's house.

What this does *not* do is bound how long a slot is held: that is the duration of the work,
and an inference that never returns holds its slot until it does. There is no timeout here
because there is none in `ComputeExecutor` either, and inventing one in the ledger would put
the deadline in the wrong place — a stage that should be abandoned is the executor's
question, not the accountant's.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from thursday_core.logging import get_logger

log = get_logger(__name__)

#: One heavy model at a time per machine. See the module docstring: a second concurrent
#: inference on one GPU is slower and sometimes fatal, not faster.
DEFAULT_LIMIT = 1


class DeviceCapacity:
    """The ledger of what this process has sent to each machine and not had back.

    Cloud targets (`device_id is None`) are counted for observability and never bounded.
    """

    def __init__(self, *, default_limit: int = DEFAULT_LIMIT) -> None:
        if default_limit < 1:
            # A limit of zero is not "no concurrency", it is "no work ever runs". Refusing
            # here beats a task that hangs on a semaphore nobody can release.
            raise ValueError("default_limit must be at least 1")
        self._default = default_limit
        self._limits: dict[UUID, int] = {}
        self._counts: dict[UUID | None, int] = {}
        self._slots: dict[UUID, asyncio.Semaphore] = {}

    # ------------------------------------------------------------------ what is known

    def limit(self, device_id: UUID | None) -> int:
        """How many of Thursday's jobs this machine may carry at once."""
        if device_id is None:
            return 0  # the cloud is unbounded; 0 reads as "no limit of ours"
        return self._limits.get(device_id, self._default)

    def in_flight(self, device_id: UUID | None) -> int:
        """Dispatched by this process and not yet returned."""
        return self._counts.get(device_id, 0)

    def at_limit(self, device_id: UUID | None) -> bool:
        if device_id is None:
            return False
        return self.in_flight(device_id) >= self.limit(device_id)

    def set_limit(self, device_id: UUID, limit: int) -> None:
        """Tell the ledger a machine can carry more (or less) than the default.

        Changing a limit does not disturb work already running: the new bound applies to
        the next job that asks for a slot. Shrinking a limit below what is in flight is
        therefore a promise about the future rather than a recall, which is the only
        honest thing it can be — the jobs are already on the machine.
        """
        if limit < 1:
            raise ValueError("a device limit must be at least 1")
        self._limits[device_id] = limit
        existing = self._slots.pop(device_id, None)
        if existing is not None and self.in_flight(device_id) == 0:
            # Rebuilding a semaphore with waiters would strand them, so this only replaces
            # an idle one. A busy device picks the new limit up once it drains.
            self._slots[device_id] = asyncio.Semaphore(limit)

    def snapshot(self) -> dict[str, dict[str, int]]:
        """What the owner (or a health check) is shown about Thursday's own load."""
        return {
            str(device): {"in_flight": count, "limit": self.limit(device)}
            for device, count in sorted(self._counts.items(), key=lambda kv: str(kv[0]))
            if count
        }

    # ------------------------------------------------------------------ the guarantee

    @asynccontextmanager
    async def hold(self, device_id: UUID | None) -> AsyncIterator[None]:
        """Hold a slot on `device_id` for the duration of the block.

        Waits when the machine is full. Waiting is right: the work is admissible and the
        machine is busy, and a task that failed because the house was busy would be a
        worse answer than one that took longer.
        """
        if device_id is None:
            self._counts[None] = self._counts.get(None, 0) + 1
            try:
                yield
            finally:
                self._counts[None] -= 1
            return

        slot = self._slots.get(device_id)
        if slot is None:
            slot = asyncio.Semaphore(self.limit(device_id))
            self._slots[device_id] = slot

        waited = self.at_limit(device_id)
        if waited:
            log.info(
                "device_at_capacity",
                device_id=str(device_id),
                in_flight=self.in_flight(device_id),
                limit=self.limit(device_id),
            )
        async with slot:
            self._counts[device_id] = self._counts.get(device_id, 0) + 1
            try:
                yield
            finally:
                self._counts[device_id] -= 1
