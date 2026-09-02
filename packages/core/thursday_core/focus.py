"""Which machine the conversation is about (§22, V8).

Two different questions get confused constantly, and Thursday has to keep them apart:

* **Where is the owner?** — the device they are speaking from. `WorldState.active_device_id`.
* **Which machine are we talking about?** — what "it" means in the next sentence.

They are usually the same, and the interesting cases are exactly the ones where they are
not::

    (on the phone)  "Thursday คอมที่บ้านเปิดอยู่ไหม"     → about Home-PC
    (on the phone)  "เปิด Chrome ให้หน่อย"                 → still about Home-PC

The second sentence names no device. Falling back to the device the owner is holding — which
is what happens without this module — opens Chrome on their phone. Not a failed command: a
command that succeeded on the wrong machine, reported as success, with the owner none the
wiser until they walk into the other room.

So a device that gets named becomes the *focus* of that conversation, and stays the focus
for the next few turns. Three properties keep that from becoming its own kind of wrong:

**It expires.** A device named twenty minutes ago is not what "it" means now.
`FOCUS_TTL_SECONDS` is short on purpose — long enough for the follow-up someone actually
says next, too short to reach across a change of subject.

**It is per conversation.** Focus lives against a session id, so a conversation on the
laptop cannot steer one on the phone.

**It is always said out loud.** `Focus.should_announce` is true whenever the focus, rather
than the sentence, chose the machine — and the caller must name the device in its reply.
An action landing somewhere the owner did not name, silently, is the failure this module is
supposed to prevent, not one it is allowed to introduce.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from thursday_core.logging import get_logger

log = get_logger(__name__)

#: How long a named device stays the subject of the conversation.
#:
#: Three minutes is a judgement call and worth stating as one. Too short and the feature
#: does not exist — the owner asks about the home PC, thinks for a moment, and the follow-up
#: goes to the wrong machine anyway. Too long and a device named at the start of a long
#: conversation silently captures an unrelated command later on. Three minutes covers "ask,
#: then say the next thing", which is the pattern this exists for.
FOCUS_TTL_SECONDS = 180.0


@dataclass(frozen=True)
class Focus:
    """A device the conversation is currently about."""

    device_id: UUID
    device_name: str
    #: Why this device is in focus, in words, for the audit trail and for the reply.
    reason: str
    set_at: datetime
    #: True when the focus was inherited from an earlier turn rather than set by this one.
    #: The caller must name the device in its reply when this is true.
    should_announce: bool = False

    def expired(self, *, now: datetime | None = None, ttl_s: float = FOCUS_TTL_SECONDS) -> bool:
        return (now or datetime.now(UTC)) - self.set_at > timedelta(seconds=ttl_s)


class DeviceFocus:
    """Per-conversation memory of which machine is being talked about."""

    def __init__(self, *, ttl_s: float = FOCUS_TTL_SECONDS) -> None:
        self._by_session: dict[UUID, Focus] = {}
        self._ttl_s = ttl_s

    def remember(
        self,
        session_id: UUID,
        *,
        device_id: UUID,
        device_name: str,
        reason: str,
        now: datetime | None = None,
    ) -> Focus:
        focus = Focus(
            device_id=device_id,
            device_name=device_name,
            reason=reason,
            set_at=now or datetime.now(UTC),
        )
        self._by_session[session_id] = focus
        log.debug("device_focus_set", device=device_name, reason=reason)
        return focus

    def current(self, session_id: UUID | None, *, now: datetime | None = None) -> Focus | None:
        """The device this conversation is about, or None.

        What comes back is flagged `should_announce`, because by the time anyone reads it
        the focus is being *inherited* — the sentence in hand did not name a device.
        """
        if session_id is None:
            return None
        focus = self._by_session.get(session_id)
        if focus is None:
            return None
        if focus.expired(now=now, ttl_s=self._ttl_s):
            log.debug("device_focus_expired", device=focus.device_name)
            self._by_session.pop(session_id, None)
            return None
        return Focus(
            device_id=focus.device_id,
            device_name=focus.device_name,
            reason=focus.reason,
            set_at=focus.set_at,
            should_announce=True,
        )

    def clear(self, session_id: UUID | None) -> None:
        """Drop the focus — said "this machine", changed device, or finished the subject."""
        if session_id is not None and self._by_session.pop(session_id, None) is not None:
            log.debug("device_focus_cleared", session_id=str(session_id))

    def __len__(self) -> int:
        return len(self._by_session)
