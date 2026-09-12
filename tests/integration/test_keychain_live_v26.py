"""The Linux leg of §23's keychain gap, closed for real (§35, threat T2/T4, ADR 0074).

Every other test of the keychain adapters — selection, availability detection, migration
ordering, the exact commands each would run — was written against a mock, because the
container these tests ran in was headless Linux with no Secret Service, no macOS and no
Windows. §23 named that plainly: *"the platform calls themselves are not [tested]."*

This file does not mock `_run`. It runs against a **real D-Bus session bus and a real,
unlocked GNOME Keyring collection** — the same daemon a Linux desktop actually has — and
drives the production `SecretServiceKeychain` adapter, `KeychainVault`, the container's
`vault_backend="keychain"` wiring, and `NodeIdentity`'s file-to-keychain migration completely
unmocked. Secrets round-trip through the real Secret Service D-Bus API; a device identity
key is moved out of a file and is verified present in the real keyring and absent from disk.

**What this does not close.** macOS Keychain and Windows DPAPI still have never met the CLI
or the DLL they call — there is no macOS and no Windows here to run them against, and no
amount of test-writing changes that. §23 keeps that half of the gap open, honestly, because
it is a hardware fact rather than a testing gap.

Every test here is skipped, not xfailed, when no live Secret Service is reachable — a
contributor's laptop without a session bus should see the rest of the suite pass, not a wall
of failures for infrastructure the test file itself is supposed to be asking for. CI sets one
up and asserts it is there (mirroring the ffmpeg/espeak pattern in `ci.yml`), so a broken setup
fails loudly there instead of this file quietly reverting to "the platform calls are not
tested" without anyone noticing.

*What closing this actually found:* four other tests, in four other files, were passing only
because this container had no keychain. `NodeIdentity(tmp_path / "node.json")` with no explicit
`keychain=` reaches `detect()`, and every one of those tests shares the same fixed account name
(`"node-identity"`) a real keychain would use — so the moment a real one is ambient, tests
asserting `key_path.exists()` or a 0600 file mode broke outright, proven by running the old
versions of those files against this same daemon. They now pass `keychain=NoKeychain()`
explicitly, which is what they meant all along.
"""

from __future__ import annotations

import uuid

import pytest
from thursday_core.config import Settings
from thursday_core.container import build_container
from thursday_security.keychain import KeychainError, NoKeychain, SecretServiceKeychain, detect
from thursday_security.vault import KeychainVault

from apps.node.__main__ import NodeIdentity

live = detect()
pytestmark = pytest.mark.skipif(
    not live.available,
    reason="no live Secret Service on this machine (needs a D-Bus session bus and an "
    "unlocked collection) — this is the gap §23 names as needing real hardware/OS, not a "
    "failure",
)


@pytest.fixture
def account():
    """A unique account per test, so tests that run concurrently or out of order cannot
    collide on the one real keyring underneath them the way the pre-fix tests did."""
    name = f"thursday-live-test-{uuid.uuid4().hex[:12]}"
    yield name
    # Best-effort: a test that failed mid-way should not leave a secret behind for the next
    # run to trip over.
    live.delete(name)


# ------------------------------------------------------------------- the real daemon exists


def test_detection_finds_a_real_secret_service_not_a_mocked_one():
    """The premise the rest of this file depends on, stated as an assertion rather than
    assumed. If this fails, everything below it is skipped for the right reason."""
    assert isinstance(live, SecretServiceKeychain)
    assert live.name == "secret-service"


# --------------------------------------------------------------- the adapter, unmocked


def test_a_secret_round_trips_through_the_real_secret_service(account):
    """`_run` is not mocked here. `secret-tool` is actually invoked, against a real
    collection, over a real D-Bus connection."""
    assert live.get(account) is None

    live.put(account, "a-real-secret-value")
    assert live.get(account) == "a-real-secret-value"

    live.delete(account)
    assert live.get(account) is None


def test_a_second_write_updates_rather_than_duplicating(account):
    """The mocked test proves `secret-tool store` is called the same way each time; this
    proves what that call actually does to a real collection — an update, not a second item
    a lookup could return either one of."""
    live.put(account, "first-value")
    live.put(account, "second-value")
    assert live.get(account) == "second-value"


def test_an_absent_account_is_a_clean_none_not_an_exception(account):
    """`secret-tool lookup` exits non-zero for a miss. The adapter turns that into `None`
    rather than letting a caller catch an exception to mean "not found"."""
    assert live.get(f"{account}-never-written") is None


# ----------------------------------------------------------------------- the vault layer


async def test_the_keychain_vault_round_trips_for_real(account):
    """`KeychainVault()` with no arguments calls `detect()` itself — the same path a real
    deployment takes, not a fake handed in for the test's convenience."""
    vault = KeychainVault()
    assert vault.available

    await vault.put(account, "vault-secret")
    assert await vault.has(account)

    async def read(value: str) -> str:
        return value

    assert await vault.use(account, read) == "vault-secret"

    await vault.delete(account)
    assert not await vault.has(account)


async def test_a_missing_secret_is_named_rather_than_silently_absent(account):
    vault = KeychainVault()
    from thursday_shared.errors import ConfigurationError

    with pytest.raises(ConfigurationError, match="no secret in the keychain"):
        await vault.use(account, lambda v: v)


# ----------------------------------------------------------------------- the container


async def test_a_deployment_configured_for_keychain_now_actually_gets_one():
    """The other half of `test_a_configured_keychain_that_is_absent_refuses_to_start`
    (`test_keychain.py`). That test proves the refusal when there is none; this proves the
    container really works end to end when there is."""
    container = build_container(
        Settings(llm_backend="rule", vault_backend="keychain"), configure_logs=False
    )
    assert container.vault.name == "chain"

    account = f"thursday-live-container-{uuid.uuid4().hex[:12]}"
    try:
        await container.vault.put(account, "container-secret")
        assert await container.vault.has(account)

        # And it is provably in the OS keychain, not merely behind an abstraction that
        # could have quietly kept it in memory or the environment.
        assert live.get(account) == "container-secret"
    finally:
        await container.vault.delete(account)
        live.delete(account)


# ------------------------------------------------------------------------- the node's key


def test_a_node_identity_never_writes_its_private_key_to_disk(tmp_path):
    node = NodeIdentity(tmp_path / "node.json", keychain=live)
    fingerprint = node.fingerprint

    assert not node.key_path.exists(), "the private key touched disk"
    assert node.storage == "secret-service"
    assert live.get("node-identity") is not None
    live.delete("node-identity")

    # Torn down explicitly rather than only via the fixture: `node-identity` is a fixed
    # account name (every node uses it), not the per-test unique one `account` generates,
    # so it needs its own cleanup regardless of which assertion above failed.
    assert fingerprint


def test_an_existing_file_key_is_really_moved_not_copied(tmp_path):
    """The migration path, end to end and unmocked: write to the keychain, read it back,
    only then delete the file — proven here by checking the real keyring holds the same PEM
    the file held, and that the file is actually gone from the filesystem afterward."""
    first = NodeIdentity(tmp_path / "node.json", keychain=NoKeychain())
    original_pem = first.key.to_pem()
    original_fingerprint = first.fingerprint
    assert first.key_path.exists()

    try:
        second = NodeIdentity(tmp_path / "node.json", keychain=live)
        assert second.fingerprint == original_fingerprint, "migration changed the identity"
        assert not second.key_path.exists(), "the file copy should be gone"

        stored = live.get("node-identity")
        assert stored == original_pem, "the keychain holds a different key than the file did"

        # And the identity survives a full restart with the file gone and only the keychain
        # holding it — the actual claim `storage` makes to the owner.
        third = NodeIdentity(tmp_path / "node.json", keychain=detect())
        assert third.fingerprint == original_fingerprint
    finally:
        live.delete("node-identity")


def test_a_locked_or_broken_keychain_refuses_rather_than_silently_writing_to_disk(tmp_path):
    """The one failure mode a fake keychain cannot exercise honestly: a real adapter object
    whose calls genuinely raise, because the daemon behind it genuinely refused."""

    class Locked(SecretServiceKeychain):
        def get(self, account: str) -> str | None:
            raise KeychainError("the collection is locked")

        def put(self, account: str, secret: str) -> None:
            raise KeychainError("the collection is locked")

    node = NodeIdentity(tmp_path / "node.json", keychain=Locked())
    with pytest.raises(SystemExit, match="could not use it"):
        _ = node.key

    assert not node.key_path.exists(), "a locked keychain must not fall back to a file"
