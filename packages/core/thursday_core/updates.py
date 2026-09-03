"""Software update (§120, Sprint 48).

An updater is the most dangerous component in a system like this. Everything else is bounded
by the permission engine; an update *replaces the permission engine*. Whatever it installs
runs with Thursday's full privileges on the owner's machine, so every design choice here is
made assuming the worst input.

The specification gives one rule outright — *never execute an arbitrary update URL supplied by
a model* — and the way to keep it is not to remember it. It is to build a component where
there is no parameter a URL could arrive in:

**Where updates come from is configuration.** The channel's base URL and the release signing
key are `Settings`, fixed before the process starts. Nothing at runtime supplies either: not a
model, not a document, not an API caller, not the manifest itself. `apply` takes a `Release`
that came from the configured source; there is no overload that takes a string.

**A manifest cannot redirect the download.** Even a correctly signed manifest may only name
artifacts *under* the pinned base URL. This is the part that is easy to leave out: pinning the
manifest URL and then trusting the URLs inside it means whoever controls the manifest controls
where the code comes from, and a signature over "download this from somewhere else" is a valid
signature.

**Signature before anything else.** The artifact is verified against the pinned public key
before it is unpacked, let alone run. A checksum alone would only prove the file arrived
intact from whoever sent it.

**Applying is never automatic.** `install_component` is already on `NEVER_AUTOMATIC` (ADR
0028) — self-recovery may restore a capability, never widen one, and installing new code is
the widest possible change. An update is proposed to the owner and applied when they say so.

**A backup is taken first.** Sprint 47 exists partly for this moment: the update that goes
wrong is the one you cannot undo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

from thursday_security.keys import PublicKey

from thursday_core.logging import get_logger

log = get_logger(__name__)


class UpdateError(Exception):
    """An update that will not proceed. The message is safe to show the owner."""


def parse_version(text: str) -> tuple[int, ...]:
    """``"0.2.10"`` → ``(0, 2, 10)``, for comparison that is not alphabetical.

    Alphabetical ordering makes 0.2.10 older than 0.2.9, which turns a security release into
    one the updater declines to install.
    """
    parts: list[int] = []
    for chunk in text.strip().lstrip("v").split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    if not parts:
        raise UpdateError(f"{text!r} is not a version number")
    return tuple(parts)


@dataclass(frozen=True)
class Release:
    """One published build, as named by a signed manifest.

    Frozen, and constructed only by `UpdateService` from a manifest it fetched. A `Release`
    a caller assembled is a caller choosing where code comes from.
    """

    version: str
    url: str
    sha256: str
    signature: str
    notes: str = ""
    published_at: datetime | None = None
    critical: bool = False

    @property
    def number(self) -> tuple[int, ...]:
        return parse_version(self.version)


class ReleaseSource(Protocol):
    """Where a manifest of available releases comes from (ADR 0001: a port, two adapters)."""

    #: The base every artifact must live under. Configuration, not manifest content.
    base_url: str

    def fetch(self) -> dict: ...


@dataclass
class LocalReleaseSource:
    """A manifest from a file. The offline adapter, and what the tests use.

    Also the honest deployment for an air-gapped install: the owner puts the manifest and the
    artifact where the config points, and the same signature check applies.
    """

    path: Path
    base_url: str = ""

    def fetch(self) -> dict:
        import json

        if not self.path.exists():
            raise UpdateError(f"no update manifest at {self.path}")
        try:
            return dict(json.loads(self.path.read_text(encoding="utf-8")))
        except ValueError as exc:
            raise UpdateError(f"the update manifest at {self.path} is not readable: {exc}") from exc


@dataclass
class PinnedHttpReleaseSource:
    """A manifest over HTTPS, from the one URL this deployment was configured with.

    `base_url` is a constructor argument fed from `Settings` — there is no setter, and no
    method takes a URL. Changing where this deployment looks for updates is an act of
    configuration by whoever runs it, not something the running system can do to itself.
    """

    base_url: str
    timeout_s: float = 15.0

    def __post_init__(self) -> None:
        parsed = urlparse(self.base_url)
        if parsed.scheme != "https":
            # http would let anyone on the path serve the manifest. The signature would still
            # be checked, but a downgrade to an older signed release is a real attack and
            # transport security is what stops it.
            raise UpdateError("the update channel must be https")

    def fetch(self) -> dict:
        import httpx

        url = self.base_url.rstrip("/") + "/manifest.json"
        try:
            response = httpx.get(url, timeout=self.timeout_s, follow_redirects=False)
            response.raise_for_status()
            return dict(response.json())
        except Exception as exc:  # httpx errors, JSON errors, all the same to the caller
            raise UpdateError(f"could not reach the update channel: {exc}") from exc


@dataclass
class UpdateState:
    """What the owner is told, and what a UI draws."""

    current: str
    latest: str | None = None
    available: bool = False
    critical: bool = False
    notes: str = ""
    checked_at: datetime | None = None
    problem: str = ""
    history: list[dict] = field(default_factory=list)


class UpdateService:
    """Checks for updates, verifies them, and applies them when the owner says so."""

    def __init__(
        self,
        *,
        current_version: str,
        source: ReleaseSource | None = None,
        signing_key: str = "",
        backups: Any = None,
        install: Any = None,
    ) -> None:
        self._current = current_version
        self._source = source
        #: The release signing public key, from configuration. A key that arrived with the
        #: manifest would be a manifest signing itself.
        self._key = PublicKey(encoded=signing_key) if signing_key else None
        self._backups = backups
        #: What actually swaps the code over. Injected, and absent by default: this package
        #: decides *whether* an update may proceed, and a platform installer decides how.
        self._install = install
        self._history: list[dict] = []

    # ------------------------------------------------------------------ checking

    def check(self, *, now: datetime | None = None) -> UpdateState:
        """Ask the configured channel what exists. Reads; changes nothing."""
        state = UpdateState(current=self._current, checked_at=now or datetime.now(UTC))
        if self._source is None:
            state.problem = "no update channel is configured for this deployment"
            return state

        try:
            release = self.latest()
        except UpdateError as exc:
            state.problem = str(exc)
            return state

        state.latest = release.version
        state.notes = release.notes
        state.critical = release.critical
        state.available = release.number > parse_version(self._current)
        state.history = list(self._history)
        return state

    def latest(self) -> Release:
        """The newest release the channel offers, fully checked before it is returned.

        Every rejection happens here rather than at apply time, so a `Release` that exists is
        one that has already passed the URL check — a caller cannot hold an unchecked one.
        """
        if self._source is None:
            raise UpdateError("no update channel is configured for this deployment")

        manifest = self._source.fetch()
        releases = manifest.get("releases") or []
        if not releases:
            raise UpdateError("the update channel lists no releases")

        best: Release | None = None
        for row in releases:
            try:
                release = Release(
                    version=str(row["version"]),
                    url=str(row["url"]),
                    sha256=str(row["sha256"]),
                    signature=str(row.get("signature", "")),
                    notes=str(row.get("notes", "")),
                    critical=bool(row.get("critical", False)),
                    published_at=(
                        datetime.fromisoformat(row["published_at"])
                        if row.get("published_at")
                        else None
                    ),
                )
            except (KeyError, TypeError, ValueError) as exc:
                log.warning("release_entry_ignored", error=str(exc))
                continue

            self._check_url(release)
            if best is None or release.number > best.number:
                best = release

        if best is None:
            raise UpdateError("no usable release was found in the manifest")
        return best

    # ------------------------------------------------------------------ verifying

    def verify(self, release: Release, artifact: bytes) -> None:
        """Refuse anything that is not this release, signed by the pinned key.

        Raises rather than returning a verdict: there is exactly one thing a caller may do
        with a failed verification, and returning a boolean invites the caller who forgets to
        look at it.
        """
        import hashlib

        if self._key is None:
            # Fail closed. A deployment that configured no signing key cannot check anything,
            # and guessing that it meant "accept whatever arrives" is how an updater becomes
            # the attack.
            raise UpdateError("no release signing key is configured; refusing to install")

        digest = hashlib.sha256(artifact).hexdigest()
        if digest != release.sha256:
            raise UpdateError("the downloaded file is not the one the manifest describes")

        # Signed over the version *and* the digest together, so a signature cannot be moved
        # from one release to another, and a valid old signature cannot bless new bytes.
        payload = signing_payload(version=release.version, sha256=release.sha256, url=release.url)
        if not self._key.verify(payload, release.signature):
            raise UpdateError("this release is not signed by the key this deployment trusts")

    # ------------------------------------------------------------------ applying

    def apply(
        self,
        release: Release,
        artifact: bytes,
        *,
        confirm: bool = False,
        allow_downgrade: bool = False,
        now: datetime | None = None,
    ) -> dict:
        """Install a release. Refused unless the owner confirmed it.

        Note the shape: a `Release` and its bytes, never a URL. There is no parameter here
        that a model, a document or an API caller could put a download location into, which
        is what makes §120 a property of the code rather than a rule someone remembers.
        """
        if not confirm:
            raise UpdateError(
                "installing an update replaces the code that enforces every other rule; "
                "it needs the owner's explicit approval"
            )

        self._check_url(release)
        self.verify(release, artifact)

        if release.number < parse_version(self._current) and not allow_downgrade:
            # Downgrades are how a signed *old* release becomes an attack: the version with
            # the bug that was fixed is still correctly signed for ever.
            raise UpdateError(
                f"{release.version} is older than the installed {self._current}; "
                "installing it would undo fixes already applied"
            )

        backup: str | None = None
        if self._backups is not None:
            # Sprint 47 exists partly for this moment: the update that goes wrong is the one
            # you cannot undo.
            path = Path(f"pre-update-{release.version}.json")
            try:
                self._backups.create(path)
                backup = str(path)
            except Exception as exc:
                raise UpdateError(f"refusing to update without a backup first: {exc}") from exc

        if self._install is None:
            raise UpdateError(
                "this build has no installer wired up; the update was verified but not applied"
            )

        try:
            self._install(release, artifact)
        except Exception as exc:
            log.warning("update_failed", version=release.version, error=str(exc))
            self._record(release, ok=False, detail=str(exc), backup=backup, now=now)
            raise UpdateError(
                f"the update to {release.version} failed and was not applied: {exc}"
            ) from exc

        self._current = release.version
        self._record(release, ok=True, detail="installed", backup=backup, now=now)
        log.warning("update_applied", version=release.version, backup=backup)
        return {"version": release.version, "backup": backup}

    @property
    def current(self) -> str:
        return self._current

    def history(self) -> list[dict]:
        return list(self._history)

    # ------------------------------------------------------------------ internals

    def _check_url(self, release: Release) -> None:
        """An artifact must live under the configured base URL.

        The check that is easy to skip. Pinning where the *manifest* comes from and then
        trusting the URLs inside it hands control of the download to whoever controls the
        manifest — and a signature over "fetch this from somewhere else" is a valid
        signature. Configuration says where code comes from; content never does.
        """
        base = getattr(self._source, "base_url", "") or ""
        if not base:
            # A source with no base (the local file adapter) still refuses anything remote:
            # an air-gapped install downloading from the internet is not air-gapped.
            if urlparse(release.url).scheme in {"http", "https"}:
                raise UpdateError(
                    f"{release.version} names a remote artifact and this deployment has no "
                    "configured update host"
                )
            return

        prefix = base.rstrip("/") + "/"
        if not release.url.startswith(prefix):
            log.warning("release_url_rejected", version=release.version)
            raise UpdateError(f"{release.version} points outside this deployment's update channel")

    def _record(
        self, release: Release, *, ok: bool, detail: str, backup: str | None, now: datetime | None
    ) -> None:
        self._history.append(
            {
                "version": release.version,
                "ok": ok,
                "detail": detail,
                "backup": backup,
                "at": (now or datetime.now(UTC)).isoformat(),
            }
        )


def signing_payload(*, version: str, sha256: str, url: str) -> str:
    """What a release is signed over.

    The digest and the version together: signing only the digest lets a signature be moved
    between releases, and signing only the version lets new bytes ride an old signature. The
    URL is in it so a signed release cannot be re-pointed even by whoever serves the manifest.
    """
    return "|".join(["thursday.release.v1", version, sha256, url])
