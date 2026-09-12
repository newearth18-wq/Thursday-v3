"""Rotating the shared enrolment token (§117, V17).

Device **keys** rotate (ADR 0042). The shared enrolment token — the one secret a node uses
before it has a key of its own — did not, and §23 has carried it as an open gap since
Sprint 52.

The reason it stayed open is that the obvious rotation is a lie. Change the environment
variable, restart the core, and every node that has not yet paired is refused: the secret is
shared, so there is no per-device moment at which to hand over. Worse, there is no way to
find out whether anything still holds the old one, so the owner rotates and then waits to
see what breaks.

**So a rotation is a window, not an instant.**

    ┌── the incoming token, accepted from the moment it is set
    │
    │   ┌── the retiring token, still accepted until `retires_at`
    │   │
    ├───┴────────────────────────────►  and then refused, with the date in the reason

Three properties make that honest rather than merely convenient:

**The retiring token expires.** A second accepted secret with no end date is not a rotation,
it is two tokens. `retires_at` is required, and once it passes the old token is refused with
a message naming the date it stopped working — so a node that shows up late is told what
happened rather than that its signature is wrong.

**Every acceptance says which token it used.** `AuthOutcome.reason` names the retiring token
by fingerprint when a node enrols on it, so "is anything still using the old secret?" is a
question the logs answer. Rotating and hoping is what this replaces.

**Nothing here invents a secret.** There is no generator. A token the machine made up and
wrote down is a secret with one more copy of itself in the world, and the owner would not
know where. The incoming token is supplied, and `Rotation` only decides which supplied
values are live right now.

A paired device is untouched by any of this: it is judged against its own key, and the
token has no job for it (`device_auth.py`).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta

from thursday_shared.models import utcnow


class RotationRefused(ValueError):
    """A rotation this module will not describe as one."""


#: Below this, an overlap is theatre: the owner cannot reach the machines still holding the
#: old token before it stops working. Not a maximum — a long overlap is the owner's call and
#: their risk, and the fingerprint in the logs is how they decide it is over.
MIN_OVERLAP = timedelta(hours=1)

#: Enough to tell two secrets apart in a log line, far too little to help guess one.
FINGERPRINT_CHARS = 8


def fingerprint(token: str) -> str:
    """A short, stable label for a secret, safe to write down.

    SHA-256 truncated: the log needs to say *which* token authenticated a node, and it must
    never be able to say what the token is.
    """
    return hashlib.sha256(token.encode()).hexdigest()[:FINGERPRINT_CHARS]


@dataclass(frozen=True)
class Rotation:
    """Which enrolment tokens are live, and until when."""

    #: The token every new enrolment should be using.
    current: str
    #: The one being replaced. Empty when no rotation is in progress.
    retiring: str = ""
    #: When `retiring` stops being accepted. Required whenever `retiring` is set.
    retires_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.current:
            raise RotationRefused("ต้องมี enrolment token ปัจจุบัน")
        if not self.retiring:
            if self.retires_at is not None:
                raise RotationRefused("มีวันหมดอายุแต่ไม่มี token ที่กำลังเลิกใช้")
            return

        if self.retiring == self.current:
            # Not pedantry: this is what "rotation" looks like when somebody set the new
            # variable to the old value, and it would report a rotation in progress for ever
            # while changing nothing.
            raise RotationRefused("token ใหม่กับ token เดิมเหมือนกัน — ยังไม่ได้หมุนเวียนจริง")
        if self.retires_at is None:
            raise RotationRefused(
                "ต้องระบุวันเลิกใช้ token เดิม — token เก่าที่ไม่มีวันหมดอายุคือการมี token สองอันถาวร"
            )

    def accepts(self, now: datetime | None = None) -> tuple[str, ...]:
        """The tokens a HELLO may be signed with at this moment, current first."""
        moment = now or utcnow()
        if self.retiring and self.retires_at is not None and moment < self.retires_at:
            return (self.current, self.retiring)
        return (self.current,)

    def label(self, token: str) -> str:
        """What to call a token in a log line or a refusal."""
        if token == self.current:
            return f"token ปัจจุบัน ({fingerprint(token)})"
        if token and token == self.retiring:
            return f"token เดิมที่กำลังเลิกใช้ ({fingerprint(token)})"
        return f"token ที่ไม่รู้จัก ({fingerprint(token)})"

    def expired(self, now: datetime | None = None) -> bool:
        """True once the retiring token has stopped working, so a refusal can say so."""
        moment = now or utcnow()
        return bool(self.retiring) and self.retires_at is not None and moment >= self.retires_at

    def to_dict(self) -> dict[str, object]:
        """What the owner may see. Never the secrets themselves."""
        return {
            "current": fingerprint(self.current),
            "retiring": fingerprint(self.retiring) if self.retiring else "",
            "retires_at": self.retires_at.isoformat() if self.retires_at else None,
            "rotating": bool(self.retiring),
        }


def begin(
    current: str,
    incoming: str,
    *,
    overlap: timedelta,
    now: datetime | None = None,
) -> Rotation:
    """Start a rotation: `incoming` becomes current, `current` retires after `overlap`.

    Refuses an overlap too short to be used. The owner has to reach the machines still
    holding the old token, and an overlap measured in seconds means the rotation happens
    whether or not they did — which is the un-rotated behaviour with extra steps.
    """
    if overlap < MIN_OVERLAP:
        raise RotationRefused(
            f"ช่วงคาบเกี่ยว {overlap} สั้นเกินไป — อย่างน้อย {MIN_OVERLAP} เพื่อให้เครื่องที่ยังใช้ token เดิมตามทัน"
        )
    return Rotation(current=incoming, retiring=current, retires_at=(now or utcnow()) + overlap)
