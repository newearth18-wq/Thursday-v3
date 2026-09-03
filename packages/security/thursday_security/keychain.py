"""The OS keychain (§35, threat T2/T4).

Two things in Thursday are worth more than the files around them: the secrets the owner has
entrusted it with, and the private key that *is* a device's identity. Both were on disk in a
0600 file. That is not nothing — it stops another user on the same machine — but it stops
nothing at all once a laptop is taken, an unencrypted backup is restored, or a process running
as the owner goes looking.

Every desktop OS ships something better, and all three work the same way from here: a store
keyed by (service, account), unlocked with the user's session, encrypted at rest by the OS.

    macOS      Keychain, via the `security` CLI
    Windows    DPAPI, which encrypts to the logged-in user
    Linux      Secret Service, via `secret-tool` (libsecret)

None of them is reached through a Python dependency. Each shells out or calls the platform
API through `ctypes`, because a keychain library that must be installed is a keychain that is
absent on the machine that skipped the extra.

**Availability is asked, never assumed.** Every adapter answers `available` by doing something
real — looking for the binary, checking for a session bus, importing the DLL — because the
failure this module exists to prevent is a deployment that *believes* its secrets are in a
keychain. That belief is worse than knowing they are in a file: a known weakness gets
compensated for, and an imagined strength does not.

**Nothing here falls back quietly.** `NoKeychain` is what an unsupported machine gets, and it
refuses every operation rather than storing anywhere weaker. The decision to accept file
storage belongs to whoever configures the deployment, made once and visibly — not to this
module, made silently on every write.

*Verification note.* This container is headless Linux with no Secret Service, no macOS and no
Windows, so the three platform adapters have never been run against a real keychain. What is
tested here is selection, availability detection, the refusal to downgrade silently, and the
exact commands each adapter would run.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Protocol

from thursday_core.logging import get_logger

log = get_logger(__name__)

#: Everything Thursday stores lands under one service name, so a person looking through their
#: own keychain can see exactly what this program put there — and remove it.
SERVICE = "Thursday"

#: How long a keychain command may take before it is treated as unavailable. A prompt the
#: owner never answers must not hang the process for ever.
TIMEOUT_S = 10.0


class KeychainError(Exception):
    """A keychain that could not be read or written. The message is safe to show."""


class Keychain(Protocol):
    """A per-user secret store provided by the operating system."""

    name: str

    @property
    def available(self) -> bool: ...

    def get(self, account: str) -> str | None: ...

    def put(self, account: str, secret: str) -> None: ...

    def delete(self, account: str) -> None: ...


def _run(command: list[str], *, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run a keychain command, never logging its output.

    `capture_output` is the point: a keychain tool prints the secret on stdout, so anything
    that logged the result of these calls would move the secret into the log file this whole
    module exists to keep it out of.
    """
    return subprocess.run(  # noqa: S603 — fixed argv, no shell, no user-supplied executable
        command,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        check=False,
    )


class NoKeychain:
    """What a machine with no usable keychain gets.

    Refuses rather than falling back. Choosing to store secrets in a file is a decision for
    whoever configures the deployment — made once, visibly — not one this module makes
    silently on every write.
    """

    name = "none"

    @property
    def available(self) -> bool:
        return False

    def _refuse(self) -> None:
        raise KeychainError(
            "this machine has no keychain Thursday can use "
            "(macOS Keychain, Windows DPAPI or a Linux Secret Service)"
        )

    def get(self, account: str) -> str | None:
        self._refuse()
        return None

    def put(self, account: str, secret: str) -> None:
        self._refuse()

    def delete(self, account: str) -> None:
        self._refuse()


class MacKeychain:
    """macOS Keychain, through the `security` CLI.

    The CLI rather than the Security framework because it needs no compiled dependency and
    behaves identically to what the owner sees in Keychain Access.
    """

    name = "macos-keychain"

    @property
    def available(self) -> bool:
        return platform.system() == "Darwin" and shutil.which("security") is not None

    def get(self, account: str) -> str | None:
        result = _run(["security", "find-generic-password", "-s", SERVICE, "-a", account, "-w"])
        if result.returncode != 0:
            return None
        return result.stdout.rstrip("\n") or None

    def put(self, account: str, secret: str) -> None:
        # -U updates in place. Without it a second write adds a duplicate entry and reads
        # return whichever the keychain feels like — which is the worst kind of intermittent.
        result = _run(
            [
                "security",
                "add-generic-password",
                "-s",
                SERVICE,
                "-a",
                account,
                # The secret goes as an argument here because `security` has no stdin form
                # for this. It is visible in this process's own argv for the duration of the
                # call, which is a real if narrow exposure and the reason the file adapter
                # is not simply worse than this one on every axis.
                "-w",
                secret,
                "-U",
            ]
        )
        if result.returncode != 0:
            raise KeychainError(f"could not write to the keychain: {result.stderr.strip()}")

    def delete(self, account: str) -> None:
        _run(["security", "delete-generic-password", "-s", SERVICE, "-a", account])


class SecretServiceKeychain:
    """Linux Secret Service (GNOME Keyring, KWallet) through `secret-tool`.

    Availability is not "is Linux": a headless server has no session bus and no unlocked
    collection, and asking there would hang or fail. Both conditions are checked.
    """

    name = "secret-service"

    @property
    def available(self) -> bool:
        if platform.system() != "Linux" or shutil.which("secret-tool") is None:
            return False
        # A session bus is what a Secret Service daemon listens on. Without one there is
        # nothing to talk to, however much libsecret is installed.
        return bool(
            os.environ.get("DBUS_SESSION_BUS_ADDRESS")
            or Path(f"/run/user/{os.getuid()}/bus").exists()
        )

    def get(self, account: str) -> str | None:
        result = _run(["secret-tool", "lookup", "service", SERVICE, "account", account])
        if result.returncode != 0 or not result.stdout:
            return None
        return result.stdout

    def put(self, account: str, secret: str) -> None:
        # Through stdin, which `secret-tool store` reads — so the secret never appears in
        # this process's argv, unlike the macOS path.
        result = _run(
            [
                "secret-tool",
                "store",
                "--label",
                f"{SERVICE}: {account}",
                "service",
                SERVICE,
                "account",
                account,
            ],
            stdin=secret,
        )
        if result.returncode != 0:
            raise KeychainError(f"could not write to the keyring: {result.stderr.strip()}")

    def delete(self, account: str) -> None:
        _run(["secret-tool", "clear", "service", SERVICE, "account", account])


class WindowsKeychain:
    """Windows DPAPI: the OS encrypts to the logged-in user, and Thursday keeps the ciphertext.

    Different in shape from the other two, and the difference is worth being explicit about.
    DPAPI is not a store — it is an encrypt/decrypt pair bound to the user account — so the
    protected blob still lives in a file. What that buys is real: the file alone is useless on
    another machine or to another user, which covers the stolen-laptop and copied-backup cases
    that plain 0600 does not.
    """

    name = "windows-dpapi"

    def __init__(self, directory: Path | None = None) -> None:
        self._dir = directory or Path.home() / ".thursday" / "dpapi"

    @property
    def available(self) -> bool:
        if platform.system() != "Windows":
            return False
        try:
            import ctypes

            ctypes.windll.crypt32  # noqa: B018 — presence is the check
        except (ImportError, AttributeError, OSError):
            return False
        return True

    def _path(self, account: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in account)
        return self._dir / f"{safe}.dpapi"

    def get(self, account: str) -> str | None:
        path = self._path(account)
        if not path.exists():
            return None
        return _dpapi(path.read_bytes(), protect=False).decode("utf-8")

    def put(self, account: str, secret: str) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._path(account)
        path.touch(mode=0o600, exist_ok=True)
        path.write_bytes(_dpapi(secret.encode("utf-8"), protect=True))

    def delete(self, account: str) -> None:
        self._path(account).unlink(missing_ok=True)


def _dpapi(data: bytes, *, protect: bool) -> bytes:
    """CryptProtectData / CryptUnprotectData, through ctypes.

    Kept in one function so the Windows adapter above reads as storage rather than as an
    exercise in structure layout.
    """
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = (("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char)))

    source = Blob(
        len(data), ctypes.cast(ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_char))
    )
    result = Blob()
    call = (
        ctypes.windll.crypt32.CryptProtectData
        if protect
        else ctypes.windll.crypt32.CryptUnprotectData
    )
    #: CRYPTPROTECT_UI_FORBIDDEN — never prompt. A background service that popped a dialogue
    #: nobody is sitting in front of would hang instead of failing.
    if not call(ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(result)):
        raise KeychainError("the Windows credential store refused this operation")
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(result.pbData)


def detect(*, directory: Path | None = None) -> Keychain:
    """The best keychain this machine actually has, or `NoKeychain`.

    Asked at startup rather than guessed from `platform.system()`: a Mac without the CLI, a
    Linux box with no session bus and a Windows container without crypt32 all look like their
    platform and none of them can store a secret.
    """
    for candidate in (MacKeychain(), WindowsKeychain(directory), SecretServiceKeychain()):
        if candidate.available:
            log.info("keychain_detected", backend=candidate.name)
            return candidate
    log.info("keychain_unavailable", platform=platform.system())
    return NoKeychain()
