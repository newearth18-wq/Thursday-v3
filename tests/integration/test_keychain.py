"""The OS keychain (§35, threat T2/T4, ADR 0040).

Two things were on disk that are worth more than the files around them: the secrets the owner
entrusted to Thursday, and the private key that *is* a device's identity.

The failure these tests are mostly about is not "the keychain broke". It is a deployment that
**believes** its secrets are in a keychain when they are in the environment — which is what
`vault_backend="keychain"` did before this, silently returning the environment vault. An
imagined protection is worse than a known weakness, because the known one gets compensated for.

*Verification note.* This container is headless Linux with no Secret Service, no macOS and no
Windows. The platform adapters have never been run against a real keychain; what is tested
here is selection, availability detection, the refusal to downgrade silently, migration, and
the exact commands each adapter would run.
"""

from __future__ import annotations

import platform
import subprocess
from unittest import mock

import pytest
from thursday_core.config import Settings
from thursday_core.container import build_container
from thursday_security.keychain import (
    SERVICE,
    KeychainError,
    MacKeychain,
    NoKeychain,
    SecretServiceKeychain,
    WindowsKeychain,
    detect,
)
from thursday_security.vault import KeychainVault
from thursday_shared.errors import ConfigurationError


class FakeKeychain:
    """A keychain that works, so the callers above it can be tested on a machine with none."""

    name = "fake"

    def __init__(self, *, available: bool = True, broken: bool = False) -> None:
        self._store: dict[str, str] = {}
        self._available = available
        self._broken = broken

    @property
    def available(self) -> bool:
        return self._available

    def get(self, account: str) -> str | None:
        if self._broken:
            raise KeychainError("the keychain is locked")
        return self._store.get(account)

    def put(self, account: str, secret: str) -> None:
        if self._broken:
            raise KeychainError("the keychain is locked")
        self._store[account] = secret

    def delete(self, account: str) -> None:
        self._store.pop(account, None)


# --------------------------------------------------------------------------- not downgrading


def test_a_configured_keychain_that_is_absent_refuses_to_start():
    """The bug this closes. `vault_backend="keychain"` returned `ChainVault(EnvVault())` and
    said nothing, so a deployment that asked for the OS keychain got the environment and
    believed otherwise. Fails closed now, exactly as a missing device token does."""
    with pytest.raises(ConfigurationError, match="no keychain"):
        build_container(
            Settings(llm_backend="rule", vault_backend="keychain"), configure_logs=False
        )


def test_the_refusal_says_what_to_do_about_it():
    """A fail-closed message that does not name the two ways out is a message that gets
    worked around by deleting the check."""
    with pytest.raises(ConfigurationError) as raised:
        build_container(
            Settings(llm_backend="rule", vault_backend="keychain"), configure_logs=False
        )
    message = str(raised.value)
    assert "vault_backend='env'" in message
    assert "Keyring" in message or "KWallet" in message


def test_an_unavailable_keychain_refuses_rather_than_storing_somewhere_else():
    """`NoKeychain` is not a null object that quietly does nothing. Choosing to accept file
    storage is a decision for whoever configures the deployment, made once and visibly."""
    keychain = NoKeychain()
    assert keychain.available is False
    for call in (
        lambda: keychain.get("x"),
        lambda: keychain.put("x", "secret"),
        lambda: keychain.delete("x"),
    ):
        with pytest.raises(KeychainError, match="no keychain"):
            call()


async def test_the_vault_reports_a_secret_is_missing_rather_than_returning_nothing(tmp_path):
    vault = KeychainVault(keychain=FakeKeychain())
    with pytest.raises(ConfigurationError, match="no secret in the keychain"):
        await vault.use("absent", lambda value: _echo(value))


async def _echo(value: str) -> str:
    return value


# --------------------------------------------------------------------------- availability


def test_availability_is_asked_not_assumed_from_the_platform():
    """A Mac without the CLI, a Linux box with no session bus and a Windows container without
    crypt32 all look like their platform and none of them can store a secret."""
    with (
        mock.patch("platform.system", return_value="Darwin"),
        mock.patch("shutil.which", return_value=None),
    ):
        assert MacKeychain().available is False

    with (
        mock.patch("platform.system", return_value="Linux"),
        mock.patch("shutil.which", return_value="/usr/bin/secret-tool"),
        mock.patch.dict("os.environ", {}, clear=True),
        mock.patch("pathlib.Path.exists", return_value=False),
    ):
        assert SecretServiceKeychain().available is False


def test_a_linux_box_with_a_session_bus_and_the_tool_is_usable():
    with (
        mock.patch("platform.system", return_value="Linux"),
        mock.patch("shutil.which", return_value="/usr/bin/secret-tool"),
        mock.patch.dict("os.environ", {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/bus"}),
    ):
        assert SecretServiceKeychain().available is True


def test_detection_returns_something_that_works_or_something_that_says_it_does_not():
    """Behaviour, not a restatement of the implementation.

    The first version of this asserted `detect().available is (not Linux or has_session_bus)`
    — a copy of the code's conditions, missing one of them. It passed here, where there is no
    session bus, and failed on CI, where the runner has a bus at /run/user/1001/bus and no
    `secret-tool`. A test that restates the implementation cannot catch the implementation
    being wrong; it can only be wrong in a different place.

    So: whatever `detect` returns, `available` must be a true statement about it. Either it
    can round-trip a secret, or it refuses.
    """
    keychain = detect()

    if not keychain.available:
        with pytest.raises(KeychainError):
            keychain.put("thursday-detection-probe", "value")
        return

    # Available means available: prove it, and clean up after.
    account = "thursday-detection-probe"
    try:
        keychain.put(account, "a-probe-value")
        assert keychain.get(account) == "a-probe-value"
    finally:
        keychain.delete(account)


# --------------------------------------------------------------------------- the commands


def test_the_macos_adapter_updates_in_place_rather_than_adding_duplicates():
    """Without `-U`, a second write adds a second entry and reads return whichever the
    keychain feels like — the worst kind of intermittent."""
    with mock.patch("thursday_security.keychain._run") as run:
        run.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        MacKeychain().put("node-identity", "pem")

    argv = run.call_args[0][0]
    assert "-U" in argv
    assert SERVICE in argv


def test_the_linux_adapter_passes_the_secret_on_stdin_not_in_argv():
    """Anything in argv is visible to every other process on the machine for the life of the
    call. `secret-tool store` reads stdin, so it does not have to be."""
    with mock.patch("thursday_security.keychain._run") as run:
        run.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        SecretServiceKeychain().put("node-identity", "a-private-key")

    argv = run.call_args[0][0]
    assert "a-private-key" not in argv
    assert run.call_args.kwargs["stdin"] == "a-private-key"


def test_a_failed_write_is_raised_rather_than_reported_as_success():
    with mock.patch("thursday_security.keychain._run") as run:
        run.return_value = subprocess.CompletedProcess([], 1, stdout="", stderr="denied")
        with pytest.raises(KeychainError, match="could not write"):
            MacKeychain().put("x", "y")


def test_the_windows_adapter_is_honest_about_being_a_file_of_ciphertext(tmp_path):
    """DPAPI is an encrypt/decrypt pair bound to the user, not a store — so the blob still
    lives in a file. What it buys is that the file alone is useless on another machine."""
    store = WindowsKeychain(tmp_path)
    assert store.available is (platform.system() == "Windows")
    assert store._path("node-identity").parent == tmp_path


# --------------------------------------------------------------------------- the node's key


def identity(tmp_path, keychain):
    from apps.node.__main__ import NodeIdentity

    return NodeIdentity(tmp_path / "node.json", keychain=keychain)


def test_a_node_with_a_keychain_never_writes_its_key_to_disk(tmp_path):
    node = identity(tmp_path, FakeKeychain())
    fingerprint = node.fingerprint

    assert not node.key_path.exists(), "the key file should not exist at all"
    assert node.storage == "fake"
    assert node._keychain.get("node-identity")
    assert fingerprint == node.key.public.fingerprint


def test_the_key_is_the_same_one_across_restarts_via_the_keychain(tmp_path):
    keychain = FakeKeychain()
    original = identity(tmp_path, keychain).fingerprint
    assert identity(tmp_path, keychain).fingerprint == original


def test_an_existing_file_key_is_moved_into_the_keychain(tmp_path):
    """And the file is removed — a key left in both places is a key protected by the weaker."""
    first = identity(tmp_path, NoKeychain())
    original = first.fingerprint
    assert first.key_path.exists()

    keychain = FakeKeychain()
    second = identity(tmp_path, keychain)

    assert second.fingerprint == original, "migration must not change the device's identity"
    assert not second.key_path.exists(), "the file copy should be gone"
    assert keychain.get("node-identity")


def test_a_failed_migration_leaves_the_file_alone(tmp_path):
    """Write, read back, and only then delete. A delete that happened first would lose the
    node's identity to a keychain write that failed — and a device that loses its key has to
    be re-paired by a person standing at it."""
    from apps.node.__main__ import KeyMigrationError

    first = identity(tmp_path, NoKeychain())
    original = first.fingerprint

    class Amnesiac(FakeKeychain):
        def get(self, account: str) -> str | None:
            return None  # accepts the write, hands nothing back

    node = identity(tmp_path, Amnesiac())
    with pytest.raises(KeyMigrationError, match="did not hand it back"):
        _ = node.key

    assert node.key_path.exists(), "the only copy of the key was deleted"
    assert identity(tmp_path, NoKeychain()).fingerprint == original


def test_a_node_whose_keychain_is_locked_refuses_rather_than_falling_back(tmp_path):
    """A node that silently downgraded its own key storage would leave the owner believing
    the keychain protects an identity it never held."""
    node = identity(tmp_path, FakeKeychain(broken=True))
    with pytest.raises(SystemExit, match="could not use it"):
        _ = node.key
    assert not node.key_path.exists()


def test_a_node_without_a_keychain_uses_a_file_and_says_so(tmp_path):
    """The fallback is real and named. It stops another user on the same machine and stops
    nothing once the laptop is taken, which is why `storage` reports it."""
    import stat

    node = identity(tmp_path, NoKeychain())
    assert node.fingerprint
    assert node.storage == "file"
    assert stat.S_IMODE(node.key_path.stat().st_mode) == 0o600
