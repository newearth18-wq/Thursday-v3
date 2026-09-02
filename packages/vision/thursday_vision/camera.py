"""Camera consent and lifecycle (§51, V6).

The camera is off. That is the default, it is the state the system returns to, and it is
the only claim in this file that matters more than the rest put together.

Everything here exists to make that true in a way the owner can verify rather than trust:

* opening requires a **reason** and an explicit grant — there is no code path that turns a
  camera on as a side effect of something else;
* a grant is **narrow and expiring**, so "yes, look at this book" does not become "yes,
  watch the room";
* the indicator is **derived from the same field the capture path reads**, so a light that
  is off while a frame is being taken is not possible without changing one line, not two;
* an idle camera **closes itself**, because the failure mode of consent is not a refused
  request, it is a granted one nobody remembered to withdraw.

A camera that is on when the owner believes it is off is the worst failure this system can
have. It is worse than a wrong answer, worse than a lost task, and it is not recoverable by
apologising afterwards.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from thursday_core.logging import get_logger
from thursday_shared.errors import PermissionDenied

from thursday_vision.ports import Frame

log = get_logger(__name__)

#: How long a single grant lasts unless it is renewed. Short: this is the window in which
#: the owner asked to be seen, not a setting.
DEFAULT_GRANT_SECONDS = 120.0

#: With no capture in this long, the camera closes itself. The failure mode of consent is
#: a grant nobody withdrew, not a request that was refused.
IDLE_CLOSE_SECONDS = 30.0


class CameraState(StrEnum):
    OFF = "OFF"
    #: Granted but not yet opened — the owner has said yes and nothing is capturing.
    ARMED = "ARMED"
    #: Hardware open. The indicator is on exactly in this state.
    ACTIVE = "ACTIVE"


@dataclass
class CameraGrant:
    """Permission to look, for a reason, for a while."""

    reason: str
    granted_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None
    #: A grant for one look cannot be reused for a second.
    max_captures: int | None = None
    captures: int = 0
    granted_by: str = "owner"

    def expired(self, *, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        if self.expires_at is not None and self.expires_at <= now:
            return True
        return self.max_captures is not None and self.captures >= self.max_captures

    def describe(self) -> str:
        return f"{self.reason} (granted by {self.granted_by})"


class CameraDenied(PermissionDenied):
    """Raised when something tries to capture without a live grant."""

    code = "camera_denied"


class CameraManager:
    """Owns whether the camera may be used, and whether it currently is."""

    def __init__(
        self,
        source: Any = None,
        *,
        grant_seconds: float = DEFAULT_GRANT_SECONDS,
        idle_close_seconds: float = IDLE_CLOSE_SECONDS,
        on_state_change: Any = None,
    ) -> None:
        self._source = source
        self.grant_seconds = grant_seconds
        self.idle_close_seconds = idle_close_seconds
        self._on_state_change = on_state_change

        self._state = CameraState.OFF
        self._grant: CameraGrant | None = None
        self._last_capture: datetime | None = None
        self._captures = 0
        #: Every grant and revocation, so "when was my camera on?" has an answer.
        self.log: list[tuple[datetime, str, str]] = []

    # ------------------------------------------------------------------ state

    @property
    def state(self) -> CameraState:
        return self._state

    @property
    def indicator_on(self) -> bool:
        """What the light shows.

        Derived from the same field the capture path checks, deliberately. An indicator
        computed from a separate flag can disagree with reality, and the first time it does
        is the time it matters.
        """
        return self._state is CameraState.ACTIVE

    @property
    def grant(self) -> CameraGrant | None:
        return self._grant

    def _set_state(self, state: CameraState, why: str) -> None:
        if state is self._state:
            return
        previous, self._state = self._state, state
        self.log.append((datetime.now(UTC), f"{previous}->{state}", why))
        log.info("camera_state", **{"from": str(previous), "to": str(state), "why": why})
        if callable(self._on_state_change):
            self._on_state_change(previous, state, why)

    # ------------------------------------------------------------------ consent

    def grant_access(
        self,
        reason: str,
        *,
        seconds: float | None = None,
        max_captures: int | None = None,
        granted_by: str = "owner",
    ) -> CameraGrant:
        """Permit use, for a stated reason and a bounded window.

        A reason is required and cannot be blank: a grant nobody can describe later is a
        grant nobody can audit, and the owner reading their own camera log deserves to see
        *why* rather than a timestamp.
        """
        if not reason.strip():
            raise ValueError("a camera grant needs a reason")
        window = self.grant_seconds if seconds is None else seconds
        self._grant = CameraGrant(
            reason=reason.strip(),
            expires_at=datetime.now(UTC) + timedelta(seconds=window),
            max_captures=max_captures,
            granted_by=granted_by,
        )
        self._set_state(CameraState.ARMED, f"granted: {reason.strip()}")
        return self._grant

    async def revoke(self, *, why: str = "revoked") -> None:
        """Withdraw the grant and close the hardware. The path behind "ปิดกล้อง" (§69)."""
        self._grant = None
        await self._close(why)

    def may_capture(self, *, now: datetime | None = None) -> tuple[bool, str]:
        if self._grant is None:
            return False, "the camera is off and no access has been granted"
        if self._grant.expired(now=now):
            return False, f"the camera grant for {self._grant.reason!r} has expired"
        return True, self._grant.reason

    # ------------------------------------------------------------------ capture

    async def capture(self) -> Frame:
        """Take one frame, if allowed. Refuses rather than asking — asking is the caller's
        job, and a component that can escalate its own permission has none."""
        allowed, why = self.may_capture()
        if not allowed:
            log.warning("camera_capture_refused", reason=why)
            raise CameraDenied(why)
        if self._source is None:
            raise CameraDenied("no camera is attached to this device")

        if self._state is not CameraState.ACTIVE:
            await self._source.open()
            self._set_state(CameraState.ACTIVE, f"capturing for: {why}")

        frame = await self._source.capture()
        self._captures += 1
        self._last_capture = datetime.now(UTC)
        if self._grant is not None:
            self._grant.captures += 1
            # A one-shot grant is spent the moment it is used, not when it expires.
            if self._grant.expired():
                await self.revoke(why="grant spent")
        return frame

    async def close_if_idle(self, *, now: datetime | None = None) -> bool:
        """Close a camera nobody is using. Called by the worker's periodic sweep."""
        if self._state is not CameraState.ACTIVE:
            return False
        now = now or datetime.now(UTC)
        if self._last_capture is None:
            return False
        idle = (now - self._last_capture).total_seconds()
        if idle < self.idle_close_seconds:
            return False
        await self._close(f"idle for {idle:.0f}s")
        return True

    async def _close(self, why: str) -> None:
        if self._source is not None and self._state is CameraState.ACTIVE:
            try:
                await self._source.close()
            except Exception as exc:
                # The hardware failed to close cleanly. Report OFF anyway *and* say so
                # loudly: an indicator stuck on ACTIVE would be the more alarming lie, and
                # a camera we cannot close is a fault the owner must hear about.
                log.error("camera_close_failed", error=str(exc))
        self._set_state(CameraState.OFF, why)

    # ------------------------------------------------------------------ reporting

    def snapshot(self) -> dict:
        allowed, why = self.may_capture()
        return {
            "state": str(self._state),
            "indicator_on": self.indicator_on,
            "may_capture": allowed,
            "reason": why,
            "grant": self._grant.describe() if self._grant else None,
            "expires_at": (
                self._grant.expires_at.isoformat()
                if self._grant and self._grant.expires_at
                else None
            ),
            "captures": self._captures,
            "camera": getattr(self._source, "name", None),
        }

    def recent_log(self, limit: int = 20) -> list[dict]:
        """ "When was my camera on?" — answerable, and by the owner rather than by a
        support ticket."""
        return [
            {"at": at.isoformat(), "transition": transition, "why": why}
            for at, transition, why in self.log[-limit:]
        ]


class CameraSweeper:
    """Closes idle cameras on a timer. A separate object because the guarantee must not
    depend on anyone remembering to call `close_if_idle`."""

    def __init__(self, manager: CameraManager, *, interval_s: float = 5.0) -> None:
        self._manager = manager
        self.interval_s = interval_s
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="camera:sweeper")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.interval_s)
            try:
                await self._manager.close_if_idle()
            except Exception as exc:
                log.warning("camera_sweep_failed", error=str(exc))
