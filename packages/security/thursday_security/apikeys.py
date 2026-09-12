"""Rotating a provider API key (§117, V18).

The last of §117's three rotations that this repository can honestly close. It is a smaller
mechanism than the enrolment window (ADR 0066) and it fails in a completely different way.

An enrolment token is shared, so the danger is cutting off machines that still hold the old
one. A provider key is held by one party — Thursday — so nothing is cut off by replacing it.
The danger is that **the replacement does not work**: a key that was copied short, pasted
with a newline, revoked before it was installed, or belongs to a different account. Write it
over the working one and Thursday is cut off from the provider, having discarded the only
credential that worked, and finds out at the next thing the owner asks for.

So the order is fixed and the whole module is about that order:

    1. prove the incoming key works       ← a real call to the provider, not a format check
    2. keep the outgoing key, under its own handle
    3. only then make the incoming key the live one
    4. tell the owner what Thursday cannot do: revoke the old key at the provider

**Step one is a real call.** A format check — starts with the right prefix, right length —
is the kind of verification this project exists not to ship. It passes for a revoked key, a
key from another account, and a key with a trailing newline, which are three of the four ways
this actually goes wrong. `verify` is a callback so the probe belongs to whatever knows how
to talk to that provider (ADR 0001); this module knows only whether it returned true.

**Step four is the honest part.** Thursday can change which key it uses. It **cannot** revoke
the old one — that is a button in the provider's console and nothing here can press it. A
rotation that reported success without saying so would leave the owner believing a
compromised key was dead when it is still live. So `Rotated.outstanding` carries the sentence,
and it is not optional.

Nothing in here returns, logs or stores a key in the clear. The only thing the owner sees is
a fingerprint, on the same terms as `enrolment.py`: enough to tell two keys apart in a log
line, useless for reconstructing one.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from thursday_security.enrolment import fingerprint


class KeyRotationRefused(ValueError):
    """A rotation that would have left Thursday worse off than before it started."""


#: Suffix for the handle the outgoing key is kept under. Kept rather than deleted: a provider
#: that accepts the new key once and rejects it a minute later is a real failure mode, and
#: the owner's way back should not be "find the old key again".
PREVIOUS_SUFFIX = ".previous"


@dataclass(frozen=True)
class Rotated:
    """What changed, and what the owner still has to do themselves."""

    handle: str
    #: Fingerprints only. This object goes into logs and onto screens.
    now_live: str
    kept_as: str
    previous: str
    #: What Thursday cannot do. Never empty — see the module docstring.
    outstanding: str

    def to_dict(self) -> dict[str, str]:
        return {
            "handle": self.handle,
            "now_live": self.now_live,
            "kept_as": self.kept_as,
            "previous": self.previous,
            "outstanding": self.outstanding,
        }


async def rotate(
    vault: Any,
    handle: str,
    incoming: str,
    *,
    verify: Callable[[str], Awaitable[bool]],
    provider: str = "provider",
) -> Rotated:
    """Replace the key behind `handle`, but only once the new one has been proven.

    `verify` is given the candidate key and must make a real call. Returning true on a
    format check would make every guarantee here worthless, which is why the probe is
    somebody else's job and this function only records the answer.

    On any failure nothing is written: the live key is whatever it was before the call.
    """
    if not incoming or not incoming.strip():
        raise KeyRotationRefused("กุญแจใหม่ว่างเปล่า")
    if incoming != incoming.strip():
        # The single commonest way this goes wrong, and invisible in every log and screen.
        # Stripping it silently would be worse: the owner's clipboard and Thursday's stored
        # value would differ, and the next rotation would compare against the wrong thing.
        raise KeyRotationRefused("กุญแจใหม่มีช่องว่างหรือขึ้นบรรทัดใหม่ติดมาด้วย — ตรวจสอบตอนคัดลอกอีกครั้ง")

    outgoing = await _current(vault, handle)
    if outgoing is not None and outgoing == incoming:
        raise KeyRotationRefused("กุญแจใหม่เหมือนกุญแจเดิม — ยังไม่ได้หมุนเวียนจริง")

    # Before anything is written. A failure here must leave the working key working.
    if not await verify(incoming):
        raise KeyRotationRefused(f"กุญแจใหม่ใช้กับ {provider} ไม่ได้ — ยังไม่ได้เปลี่ยนอะไร กุญแจเดิมยังใช้งานอยู่")

    if outgoing is not None:
        await vault.put(handle + PREVIOUS_SUFFIX, outgoing)
    await vault.put(handle, incoming)

    return Rotated(
        handle=handle,
        now_live=fingerprint(incoming),
        kept_as=handle + PREVIOUS_SUFFIX if outgoing is not None else "",
        previous=fingerprint(outgoing) if outgoing is not None else "",
        outstanding=(
            f"เพิกถอนกุญแจเดิมที่ {provider} ด้วยตัวเอง — Thursday เปลี่ยนได้แค่ว่าจะใช้กุญแจไหน "
            "ไม่มีทางเพิกถอนกุญแจเก่าให้ได้ ถ้ายังไม่เพิกถอน กุญแจเก่าก็ยังใช้งานได้อยู่"
        ),
    )


async def roll_back(vault: Any, handle: str) -> str:
    """Put the kept key back, for a new key that worked once and then did not.

    Returns the fingerprint of the key that is live afterwards. Refuses when there is nothing
    kept, rather than leaving the caller to discover that the live key never changed.
    """
    kept = await _current(vault, handle + PREVIOUS_SUFFIX)
    if kept is None:
        raise KeyRotationRefused(f"ไม่มีกุญแจเดิมเก็บไว้สำหรับ {handle!r}")
    await vault.put(handle, kept)
    await vault.delete(handle + PREVIOUS_SUFFIX)
    return fingerprint(kept)


async def _current(vault: Any, handle: str) -> str | None:
    """Read a stored secret, without letting it escape as a return value of the vault.

    `use` hands the value to a callback precisely so it cannot be stashed; this reaches
    around that for the one operation that genuinely needs the old value — writing it to the
    keep-handle and comparing it with the incoming one. It stays local to this function and
    is never returned past `rotate`.
    """
    if not await vault.has(handle):
        return None
    held: list[str] = []

    async def take(value: str) -> None:
        held.append(value)

    await vault.use(handle, take)
    return held[0] if held else None
