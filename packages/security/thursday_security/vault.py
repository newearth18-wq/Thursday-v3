"""Secret vault (§35).

Credentials never appear in a prompt, a note, a vector store, or the database. A tool that
needs one receives a *handle*; the raw value is materialised only inside ``use()``, for the
duration of one call, and is never returned to the caller.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from typing import TypeVar

from thursday_shared.errors import ConfigurationError

T = TypeVar("T")


class InMemoryVault:
    """Development and test vault. Never use in production — nothing is encrypted at rest."""

    name = "memory"

    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self._values: dict[str, str] = dict(initial or {})
        self._access_log: list[str] = []

    async def put(self, handle: str, value: str) -> None:
        self._values[handle] = value

    async def has(self, handle: str) -> bool:
        return handle in self._values

    async def use(self, handle: str, fn: Callable[[str], Awaitable[T]]) -> T:
        if handle not in self._values:
            raise ConfigurationError(f"no secret registered for handle {handle!r}")
        self._access_log.append(handle)
        return await fn(self._values[handle])

    async def delete(self, handle: str) -> None:
        self._values.pop(handle, None)

    @property
    def access_log(self) -> list[str]:
        """Which handles were used, never their values — for the audit trail."""
        return list(self._access_log)


class EnvVault:
    """Reads from the process environment, e.g. ``THURSDAY_SECRET_ANTHROPIC_API_KEY``.

    A stepping stone to the OS keychain (DPAPI / Keychain / libsecret), which is the
    production backend. The port is identical, so swapping is a container change.
    """

    name = "env"
    prefix = "THURSDAY_SECRET_"

    def _key(self, handle: str) -> str:
        return self.prefix + handle.upper().replace("-", "_").replace(".", "_")

    async def put(self, handle: str, value: str) -> None:
        raise ConfigurationError("EnvVault is read-only; set the environment variable instead")

    async def has(self, handle: str) -> bool:
        return self._key(handle) in os.environ

    async def use(self, handle: str, fn: Callable[[str], Awaitable[T]]) -> T:
        value = os.environ.get(self._key(handle))
        if value is None:
            raise ConfigurationError(f"no secret in environment for handle {handle!r}")
        return await fn(value)

    async def delete(self, handle: str) -> None:
        os.environ.pop(self._key(handle), None)


class ChainVault:
    """Try each backend in order. Lets a keychain shadow the environment during migration."""

    name = "chain"

    def __init__(self, *vaults: object) -> None:
        if not vaults:
            raise ConfigurationError("ChainVault needs at least one backend")
        self._vaults = vaults

    async def put(self, handle: str, value: str) -> None:
        await self._vaults[0].put(handle, value)  # type: ignore[attr-defined]

    async def has(self, handle: str) -> bool:
        for vault in self._vaults:
            if await vault.has(handle):  # type: ignore[attr-defined]
                return True
        return False

    async def use(self, handle: str, fn: Callable[[str], Awaitable[T]]) -> T:
        for vault in self._vaults:
            if await vault.has(handle):  # type: ignore[attr-defined]
                return await vault.use(handle, fn)  # type: ignore[attr-defined]
        raise ConfigurationError(f"no secret registered for handle {handle!r}")

    async def delete(self, handle: str) -> None:
        for vault in self._vaults:
            await vault.delete(handle)  # type: ignore[attr-defined]
