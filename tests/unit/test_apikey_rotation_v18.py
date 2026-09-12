"""Rotating a provider API key (§117, V18).

The last of §117's three rotations this repository can honestly close, and it fails in a
completely different way from the enrolment window. Nothing gets cut off by replacing a key
only Thursday holds — what goes wrong is that the replacement does not work, and by then the
one that did is gone.
"""

from __future__ import annotations

import inspect

import pytest
from thursday_security import apikeys
from thursday_security.apikeys import (
    PREVIOUS_SUFFIX,
    KeyRotationRefused,
    roll_back,
    rotate,
)
from thursday_security.enrolment import fingerprint
from thursday_security.vault import InMemoryVault

HANDLE = "anthropic_api_key"
OLD = "sk-ant-the-one-that-works"
NEW = "sk-ant-the-replacement"


def vault(**secrets) -> InMemoryVault:
    return InMemoryVault({HANDLE: OLD, **secrets})


async def works(_key: str) -> bool:
    return True


async def refused(_key: str) -> bool:
    return False


async def stored(v: InMemoryVault, handle: str) -> str | None:
    if not await v.has(handle):
        return None
    held: list[str] = []

    async def take(value: str) -> None:
        held.append(value)

    await v.use(handle, take)
    return held[0]


# ------------------------------------------------------- the order is the whole mechanism


async def test_the_new_key_is_proven_before_the_working_one_is_touched():
    v = vault()
    calls: list[str] = []

    async def verify(key: str) -> bool:
        # The live key must still be the old one at the moment the probe runs.
        calls.append(await stored(v, HANDLE) or "")
        return True

    await rotate(v, HANDLE, NEW, verify=verify)
    assert calls == [OLD], "verification ran after the write"


async def test_a_key_that_does_not_work_changes_nothing():
    """The failure this module exists for: write it over the working one and Thursday is cut
    off from the provider, having discarded the only credential that worked."""
    v = vault()
    with pytest.raises(KeyRotationRefused, match="ใช้กับ"):
        await rotate(v, HANDLE, NEW, verify=refused)

    assert await stored(v, HANDLE) == OLD
    assert not await v.has(HANDLE + PREVIOUS_SUFFIX)


async def test_the_refusal_says_the_old_key_still_works():
    v = vault()
    with pytest.raises(KeyRotationRefused, match="กุญแจเดิมยังใช้งานอยู่"):
        await rotate(v, HANDLE, NEW, verify=refused)


async def test_a_proven_key_becomes_live_and_the_old_one_is_kept():
    """Kept rather than deleted: a provider that accepts a key once and rejects it a minute
    later is real, and the owner's way back should not be 'find the old key again'."""
    v = vault()
    result = await rotate(v, HANDLE, NEW, verify=works)

    assert await stored(v, HANDLE) == NEW
    assert await stored(v, HANDLE + PREVIOUS_SUFFIX) == OLD
    assert result.kept_as == HANDLE + PREVIOUS_SUFFIX


async def test_rotating_when_there_was_no_key_keeps_nothing():
    v = InMemoryVault()
    result = await rotate(v, HANDLE, NEW, verify=works)
    assert await stored(v, HANDLE) == NEW
    assert result.kept_as == "" and result.previous == ""


# ----------------------------------------------------------------- what Thursday cannot do


async def test_a_rotation_always_says_the_old_key_is_still_live_at_the_provider():
    """Thursday can change which key it uses. It cannot revoke the old one — that is a
    button in the provider's console. Reporting success without saying so leaves the owner
    believing a compromised key is dead."""
    result = await rotate(vault(), HANDLE, NEW, verify=works, provider="Anthropic")
    assert result.outstanding
    assert "เพิกถอน" in result.outstanding
    assert "Anthropic" in result.outstanding


async def test_the_outstanding_sentence_is_not_optional():
    result = await rotate(vault(), HANDLE, NEW, verify=works)
    assert result.to_dict()["outstanding"]


# ------------------------------------------------------------------------- no key escapes


async def test_nothing_returned_contains_a_key():
    result = await rotate(vault(), HANDLE, NEW, verify=works, provider="Anthropic")
    rendered = str(result.to_dict())
    assert OLD not in rendered and NEW not in rendered
    assert result.now_live == fingerprint(NEW)
    assert result.previous == fingerprint(OLD)


async def test_a_refusal_never_quotes_the_key():
    v = vault()
    with pytest.raises(KeyRotationRefused) as caught:
        await rotate(v, HANDLE, NEW, verify=refused)
    assert NEW not in str(caught.value) and OLD not in str(caught.value)


# ----------------------------------------------------------------------------- refusals


async def test_an_empty_key_is_refused():
    with pytest.raises(KeyRotationRefused, match="ว่างเปล่า"):
        await rotate(vault(), HANDLE, "   ", verify=works)


async def test_a_key_with_a_stray_newline_is_refused_rather_than_trimmed():
    """The commonest way this goes wrong, and invisible on every screen. Trimming silently
    would leave the owner's clipboard and Thursday's stored value different, and the next
    rotation comparing against the wrong thing."""
    with pytest.raises(KeyRotationRefused, match="ช่องว่าง"):
        await rotate(vault(), HANDLE, NEW + "\n", verify=works)


async def test_rotating_a_key_to_itself_is_refused():
    with pytest.raises(KeyRotationRefused, match="เหมือนกุญแจเดิม"):
        await rotate(vault(), HANDLE, OLD, verify=works)


async def test_a_key_to_itself_is_refused_before_the_probe_is_paid_for():
    probes: list[str] = []

    async def counting(key: str) -> bool:
        probes.append(key)
        return True

    with pytest.raises(KeyRotationRefused):
        await rotate(vault(), HANDLE, OLD, verify=counting)
    assert probes == [], "a real API call was made for a rotation that was never possible"


# ------------------------------------------------------------------------- rolling back


async def test_the_kept_key_can_be_put_back():
    v = vault()
    await rotate(v, HANDLE, NEW, verify=works)
    assert await roll_back(v, HANDLE) == fingerprint(OLD)
    assert await stored(v, HANDLE) == OLD


async def test_rolling_back_clears_the_keep_handle():
    """Otherwise a second roll-back would silently put the same key back again, and the
    owner would have no idea which one is live."""
    v = vault()
    await rotate(v, HANDLE, NEW, verify=works)
    await roll_back(v, HANDLE)
    assert not await v.has(HANDLE + PREVIOUS_SUFFIX)


async def test_rolling_back_with_nothing_kept_is_refused_rather_than_a_no_op():
    with pytest.raises(KeyRotationRefused, match="ไม่มีกุญแจเดิม"):
        await roll_back(vault(), HANDLE)


# --------------------------------------------------------------- the probe is not a guess


def test_the_module_does_not_check_a_key_by_looking_at_it():
    """A format check passes for a revoked key, a key from another account, and a key with a
    trailing newline — three of the four ways this actually goes wrong."""
    source = inspect.getsource(apikeys)
    assert "startswith(" not in source
    assert "sk-" not in source
    assert "len(incoming)" not in source
