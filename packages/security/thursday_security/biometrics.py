"""Where biometric templates live, and everywhere they must not (§9, §10, §38, §55, §56).

§9 is a list of places a template may never appear: memory, Obsidian, the vector store, the
LLM context, an agent's context, logs. §75 adds the audit trail. The list is long because a
face template is not like a password — the owner cannot change their face after a leak, so the
usual reasoning ("rotate it and move on") does not apply and the only workable posture is that
it never goes anywhere.

**So this store is deliberately hard to misuse rather than carefully used.**

    - Templates are encrypted at rest with a key held by the OS keychain, so the file on disk
      is not the secret.
    - `load_template` returns bytes that are never rendered, never serialised into a model,
      and never logged; the store's own logging records ids and never content.
    - There is no `export_all`, no `to_dict`, no `__repr__` that could carry a template into a
      traceback, and no `search`. What a caller can do is name one template and get one back.
    - `EnrolledTemplate` carries no raw sample. §7 and §8 say the raw images and audio are
      deleted once the template exists, and the type has nowhere to put them.

**Encryption here is not theatre, but it is not the main defence either.** The main defence is
that the interesting code paths do not take a template as an argument. Encryption stops a
stolen disk; the shape of the API stops the ordinary accident, which is the one that actually
happens — a debug log, a model prompt, a backup, a crash report.

§55's rule has no code because it is a rule about what not to build: this store holds
templates for authentication and nothing infers age, gender, ethnicity, health, emotion or
anything else from them. There is no field for it and no provider method that returns one.
"""

from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from thursday_core.logging import get_logger

log = get_logger(__name__)

#: The keychain account the template-encryption key lives under. One key for the store; the
#: templates are individually sealed with it, so losing it loses all of them — which is the
#: correct blast radius for "somebody wiped the keychain" and is why §44 requires a recovery
#: path that does not depend on biometrics at all.
KEY_HANDLE = "thursday-biometric-store"

#: AES-GCM: authenticated, so a tampered template fails to decrypt rather than decrypting to
#: something attacker-chosen. A template that could be edited on disk is a template an
#: attacker can replace with their own face.
_NONCE_BYTES = 12
_KEY_BYTES = 32


class BiometricError(Exception):
    """Something went wrong with a template. Never carries template content."""


@dataclass(frozen=True)
class EnrolledTemplate:
    """§38's `biometric_profiles` row. Metadata only — the template itself is sealed.

    There is deliberately no `raw` and no `sample`: §7 and §8 delete the source images and
    audio once a template exists, and a field to put them in is how that stops happening.
    """

    template_id: str
    user_id: str
    #: "face" or "voice". A string rather than an enum so a provider can add one without
    #: this module needing to know what it is.
    kind: str
    provider: str
    algorithm_version: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    revoked_at: datetime | None = None
    #: A short, non-reversible fingerprint of the sealed template. Lets an audit entry say
    #: *which* template was used without carrying any part of it (§75).
    digest: str = ""

    @property
    def active(self) -> bool:
        return self.revoked_at is None


class KeyProvider(Protocol):
    """Where the store's encryption key comes from. A Protocol so the OS keychain, a
    hardware-backed key, or a test double all satisfy it identically (§10)."""

    def get(self, account: str) -> str | None: ...
    def put(self, account: str, secret: str) -> None: ...


class SecureBiometricStore:
    """§10. Encrypted local storage for templates, and nothing else.

    Every method takes and returns identifiers. The one that returns template bytes is named
    for what it does and is called by exactly one layer — the matcher — which is the only code
    with any business holding one.
    """

    def __init__(self, *, directory: Path, keys: KeyProvider | None = None) -> None:
        self._dir = Path(directory)
        self._keys = keys
        self._profiles: dict[str, EnrolledTemplate] = {}
        self._key: bytes | None = None

    # ------------------------------------------------------------------ the key

    def _encryption_key(self) -> bytes:
        """Fetch or create the store key. Held by the OS keychain where there is one.

        Falls back to a file **only** when no keychain is configured, and says so loudly:
        a key beside the data it protects is not protecting it from anybody who has the data,
        and somebody should know that is the situation they are in.
        """
        if self._key is not None:
            return self._key

        if self._keys is not None:
            existing = self._keys.get(KEY_HANDLE)
            if existing:
                self._key = base64.b64decode(existing)
                return self._key
            fresh = AESGCM.generate_key(bit_length=_KEY_BYTES * 8)
            self._keys.put(KEY_HANDLE, base64.b64encode(fresh).decode())
            self._key = fresh
            log.info("biometric_key_created", backend="keychain")
            return self._key

        path = self._dir / "store.key"
        if path.exists():
            self._key = path.read_bytes()
        else:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._key = AESGCM.generate_key(bit_length=_KEY_BYTES * 8)
            path.write_bytes(self._key)
            os.chmod(path, 0o600)
            log.warning(
                "biometric_key_on_disk",
                reason="no keychain configured; the key sits beside the data it protects",
            )
        return self._key

    # ------------------------------------------------------------------ §10's surface

    def store_template(
        self,
        *,
        user_id: str,
        kind: str,
        template: bytes,
        provider: str,
        algorithm_version: str = "1",
    ) -> EnrolledTemplate:
        """Seal a template and record that it exists.

        `template` is the only place raw template bytes enter this module, and they leave the
        function encrypted. Nothing about them is logged.
        """
        if not template:
            raise BiometricError("refusing to store an empty template")

        template_id = f"{user_id}:{kind}"
        sealed = self._seal(template)
        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / f"{_safe(template_id)}.tpl").write_bytes(sealed)

        profile = EnrolledTemplate(
            template_id=template_id,
            user_id=user_id,
            kind=kind,
            provider=provider,
            algorithm_version=algorithm_version,
            digest=hashlib.sha256(sealed).hexdigest()[:16],
        )
        self._profiles[template_id] = profile
        # Ids and counts. Never the template, never its length — a length is a weak
        # fingerprint and there is no reason to write one down.
        log.info("biometric_enrolled", user=user_id, kind=kind, provider=provider)
        return profile

    def load_template(self, *, user_id: str, kind: str) -> bytes | None:
        """The one call that returns template bytes. Called by the matcher and nothing else."""
        template_id = f"{user_id}:{kind}"
        profile = self._profiles.get(template_id)
        if profile is None or not profile.active:
            return None
        path = self._dir / f"{_safe(template_id)}.tpl"
        if not path.exists():
            # The row says there is a template and there is not. Reported rather than
            # treated as "no enrolment", because the two have different remedies.
            log.warning("biometric_template_missing", user=user_id, kind=kind)
            return None
        try:
            return self._open(path.read_bytes())
        except InvalidTag as exc:
            # Authenticated encryption doing its job: the file was changed. This is the
            # attack where somebody swaps in their own face, and it fails closed.
            log.warning("biometric_template_tampered", user=user_id, kind=kind)
            raise BiometricError("stored template failed its integrity check") from exc

    def delete_template(self, *, user_id: str, kind: str) -> bool:
        """§57. Remove it, and mean it."""
        template_id = f"{user_id}:{kind}"
        path = self._dir / f"{_safe(template_id)}.tpl"
        existed = path.exists()
        if existed:
            path.unlink()
        self._profiles.pop(template_id, None)
        log.info("biometric_removed", user=user_id, kind=kind)
        return existed

    def rotate_template(
        self, *, user_id: str, kind: str, template: bytes, provider: str
    ) -> EnrolledTemplate:
        """Replace one. A face changes — glasses, a beard, five years — and re-enrolment has
        to be ordinary rather than an emergency."""
        self.delete_template(user_id=user_id, kind=kind)
        return self.store_template(user_id=user_id, kind=kind, template=template, provider=provider)

    def revoke(self, *, user_id: str, kind: str) -> bool:
        """Mark a template unusable without destroying it, so an owner who suspects something
        can stop it being used before deciding whether to re-enrol."""
        profile = self._profiles.get(f"{user_id}:{kind}")
        if profile is None:
            return False
        self._profiles[profile.template_id] = EnrolledTemplate(
            template_id=profile.template_id,
            user_id=profile.user_id,
            kind=profile.kind,
            provider=profile.provider,
            algorithm_version=profile.algorithm_version,
            created_at=profile.created_at,
            updated_at=datetime.now(UTC),
            revoked_at=datetime.now(UTC),
            digest=profile.digest,
        )
        return True

    # ------------------------------------------------------------------ metadata only

    def profiles(self, *, user_id: str | None = None) -> list[EnrolledTemplate]:
        """What is enrolled. Metadata, never content — this is what a settings screen reads."""
        rows = [p for p in self._profiles.values() if user_id is None or p.user_id == user_id]
        return sorted(rows, key=lambda p: (p.user_id, p.kind))

    def enrolled(self, *, user_id: str, kind: str) -> bool:
        profile = self._profiles.get(f"{user_id}:{kind}")
        return profile is not None and profile.active

    def kinds_for(self, user_id: str) -> set[str]:
        return {p.kind for p in self.profiles(user_id=user_id) if p.active}

    # ------------------------------------------------------------------ sealing

    def _seal(self, plaintext: bytes) -> bytes:
        nonce = os.urandom(_NONCE_BYTES)
        return nonce + AESGCM(self._encryption_key()).encrypt(nonce, plaintext, None)

    def _open(self, sealed: bytes) -> bytes:
        nonce, body = sealed[:_NONCE_BYTES], sealed[_NONCE_BYTES:]
        return AESGCM(self._encryption_key()).decrypt(nonce, body, None)

    # `__repr__` is overridden rather than inherited: the default would print `_profiles` and
    # `_key`, and a container repr reaches a traceback, a debugger and sometimes a log line.
    def __repr__(self) -> str:  # pragma: no cover - defensive, by design
        return f"<SecureBiometricStore profiles={len(self._profiles)}>"


def _safe(template_id: str) -> str:
    """A filename that cannot escape the store's directory or collide across users."""
    return hashlib.sha256(template_id.encode()).hexdigest()


# --------------------------------------------------------------------------- §11, §15 ports


class FaceIdentityProvider(Protocol):
    """§11. What a face implementation must offer, and the boundary it may not cross.

    Note what is absent: nothing returns an image, and nothing returns an attribute about the
    person (§55). `perform_liveness_check` is separate from `match_identity` because a
    photograph matches perfectly and is not alive — blending them into one score would rank a
    good photograph above a slightly-off real face, which is precisely backwards.
    """

    name: str

    def detect_face(self, frame: Any) -> bool: ...

    def extract_template(self, frames: list[Any]) -> bytes: ...

    def match_identity(self, frame: Any, template: bytes) -> float: ...

    def perform_liveness_check(self, frames: list[Any]) -> float: ...


class SpeakerIdentityProvider(Protocol):
    """§15. The voice equivalent, with two extra checks the face side does not need.

    `detect_replay_risk` and `detect_synthetic_voice_risk` are separate from confidence for
    the same reason liveness is on the face side: a recording of the owner *is* the owner's
    voice, and a synthetic one is designed to be. Neither is caught by matching harder.
    """

    name: str

    def detect_speech(self, audio: Any) -> bool: ...

    def extract_speaker_template(self, samples: list[Any]) -> bytes: ...

    def match_speaker(self, audio: Any, template: bytes) -> float: ...

    def perform_voice_liveness(self, audio: Any) -> float: ...

    def detect_replay_risk(self, audio: Any) -> float: ...

    def detect_synthetic_voice_risk(self, audio: Any) -> float: ...
