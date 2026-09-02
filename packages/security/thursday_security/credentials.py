"""Where device credentials survive a restart (§82, Sprint 36).

Pairing is only worth the manual step if it lasts. An in-memory registry looks fine in tests
and fails in the worst possible way in production: the core restarts, forgets every pairing,
and every paired node is *locked out* — it signs with its key, the core no longer knows the
key, and the node correctly refuses to fall back to the shared token. Not degraded service;
no service, until somebody re-pairs every machine by hand.

So the registry is a port with two adapters, per ADR 0001. `PairingService` writes through it
and knows nothing about files.

Only public material is written. The file is the public keys, the names, and when each device
was paired or revoked — which is exactly what §90 says may be stored, and none of what it
says may not. It is still written 0600, because the set of machines the owner trusts is worth
knowing to somebody attacking them even when none of it is secret.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID

from thursday_core.logging import get_logger

from thursday_security.keys import PublicKey

log = get_logger(__name__)


@dataclass(frozen=True)
class StoredCredential:
    """The public record of one paired device, in the shape the store round-trips.

    A plain record rather than the service's own `DeviceCredential` so that the storage
    format is a decision made here, and changing it does not mean changing the type the
    authenticator reads.
    """

    device_id: UUID
    public_key: str
    name: str
    os: str
    hostname: str = ""
    algorithm: str = "ed25519"
    paired_at: datetime | None = None
    revoked_at: datetime | None = None


class CredentialStore(Protocol):
    """Where paired devices are remembered between runs."""

    def load(self) -> list[StoredCredential]: ...

    def save(self, credentials: list[StoredCredential]) -> None: ...


class MemoryCredentialStore:
    """For tests, and for a core that is deliberately ephemeral."""

    def __init__(self, initial: list[StoredCredential] | None = None) -> None:
        self._rows = list(initial or [])

    def load(self) -> list[StoredCredential]:
        return list(self._rows)

    def save(self, credentials: list[StoredCredential]) -> None:
        self._rows = list(credentials)


class FileCredentialStore:
    """A JSON file under the core's data directory.

    Written by replacing a temporary file rather than in place: a core killed halfway through
    a write would otherwise leave a truncated registry, and a truncated registry locks out
    every device it lost.

    A record that cannot be read is dropped with a warning rather than taken down the whole
    core. That direction is deliberate and it is the *safe* one here: a dropped credential
    means a device has to pair again, while refusing to start means no device works at all.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> list[StoredCredential]:
        if not self.path.exists():
            return []
        try:
            rows = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("credential_store_unreadable", path=str(self.path), error=str(exc))
            return []

        credentials: list[StoredCredential] = []
        for row in rows if isinstance(rows, list) else []:
            try:
                credentials.append(
                    StoredCredential(
                        device_id=UUID(row["device_id"]),
                        public_key=row["public_key"],
                        name=row.get("name", ""),
                        os=row.get("os", ""),
                        hostname=row.get("hostname", ""),
                        algorithm=row.get("algorithm", "ed25519"),
                        paired_at=_when(row.get("paired_at")),
                        revoked_at=_when(row.get("revoked_at")),
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                log.warning("credential_record_dropped", error=str(exc))
        return credentials

    def save(self, credentials: list[StoredCredential]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            [
                {
                    "device_id": str(c.device_id),
                    "public_key": c.public_key,
                    "name": c.name,
                    "os": c.os,
                    "hostname": c.hostname,
                    "algorithm": c.algorithm,
                    "paired_at": c.paired_at.isoformat() if c.paired_at else None,
                    "revoked_at": c.revoked_at.isoformat() if c.revoked_at else None,
                }
                for c in credentials
            ],
            indent=2,
        )
        temporary = self.path.with_suffix(".tmp")
        temporary.touch(mode=0o600, exist_ok=True)
        temporary.chmod(0o600)
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, self.path)
        self.path.chmod(0o600)


def _when(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def public_key_of(row: StoredCredential) -> PublicKey:
    return PublicKey(encoded=row.public_key)
