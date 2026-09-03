"""Thursday device node.

Runs on the user's machine and dials *out* to the core, so nothing has to listen on a port
and no inbound firewall rule is needed.

    python -m apps.node --name Office-PC --core ws://127.0.0.1:8000/api/v1/device

The node's identity key is generated on first run and stored beside its config. Until the
node has paired (``--pair``) it authenticates with the shared enrolment token; afterwards it
signs with its own key and the token is not needed on this machine at all — which is the
whole point of pairing, and the reason ``--pair`` is worth running even on the first node.

In production the private key belongs in the OS keychain; the ``node.key`` file exists so
the node runs on a machine without one, and it is written 0600 and never transmitted.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import platform
import secrets
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import websockets
from thursday_core.logging import configure_logging, get_logger
from thursday_devices.node.adapters import for_current_platform
from thursday_devices.node.executor import NodeExecutor
from thursday_models.local_manager import LocalModelManager
from thursday_security.device_auth import sign, signing_payload
from thursday_security.keys import (
    PrivateKey,
    hello_payload,
    pairing_payload,
    rotation_payload,
)
from thursday_security.pinning import Pin, PinUnavailable, check_peer, peer_pin, pinned_context
from thursday_shared.models import DeviceAction
from thursday_shared.protocol import (
    CLOSE_SESSION_EXPIRED,
    ActionFrame,
    ActionResultFrame,
    Heartbeat,
    Hello,
    ShutdownFrame,
    Welcome,
    parse_frame,
)

from apps.node.diagnostics import serve_diagnostics

log = get_logger("thursday.node")

#: Must match the core's `device_shared_secret_handle`, which the EnvVault reads from
#: the same variable. One name, both sides.
TOKEN_ENV = "THURSDAY_SECRET_DEVICE_ENROLLMENT_SECRET"  # noqa: S105

#: What the node's key is called inside the keychain, so the owner can find and remove it.
KEY_ACCOUNT = "node-identity"

#: Where a key that has been generated for a rotation lives until the core accepts it.
#: Separate from the live key on purpose: until the core says yes, the live key is still
#: the one that works, and overwriting it early is how a rotation locks a machine out.
PENDING_KEY_ACCOUNT = "node-identity-pending"


class KeyMigrationError(Exception):
    """A key that could not be moved into the keychain. The file is left untouched."""


RECONNECT_BASE_S = 2.0
RECONNECT_MAX_S = 60.0

#: How long a session must have lasted for its expiry to count as routine rather than as a
#: core that is refusing this node in an expensive way.
MIN_HEALTHY_SESSION_S = 30.0


class NodeIdentity:
    """The node's device id and its private key, persisted between runs.

    Two files, deliberately. ``node.json`` is the boring one — an id, and a note of which
    pairing this node holds — and is safe to read, copy and inspect. ``node.key`` is the
    private key, written 0600, and is the one thing on this machine that proves the node is
    itself. Splitting them means the file an operator is likely to open, paste into a bug
    report or sync to a backup is not the file that is a credential.

    The enrolment token is in neither: it comes from the environment. A stolen laptop should
    yield this device's identity, which the owner can revoke, rather than a token that lets
    the thief register a second machine as the owner's.
    """

    def __init__(self, path: Path, *, keychain: object | None = None) -> None:
        self.path = path
        self.key_path = path.with_suffix(".key")
        self.data = self._load()
        self._key: PrivateKey | None = None
        from thursday_security.keychain import detect

        self._keychain = keychain or detect()

    def _load(self) -> dict:
        if self.path.exists():
            data = json.loads(self.path.read_text())
            # Older nodes stored a random `secret` that the core never checked. It
            # authenticated nothing, so it is dropped rather than migrated.
            if data.pop("secret", None) is not None:
                self.path.write_text(json.dumps(data, indent=2))
                log.info("node_identity_secret_dropped", path=str(self.path))
            return data
        identity = {"device_id": str(uuid.uuid4())}
        self._write(identity)
        log.info("node_identity_created", path=str(self.path))
        return identity

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2))
        self.path.chmod(0o600)

    # ------------------------------------------------------------------ the key

    @property
    def key(self) -> PrivateKey:
        """This node's private key, generated on first use and never sent anywhere.

        The OS keychain if this machine has one, and a 0600 file if it does not. The file is
        a real fallback rather than a pretend one: it stops another user on the same machine
        and stops nothing once a laptop is taken, and `storage` says which the node is using
        so nobody has to guess.
        """
        if self._key is not None:
            return self._key

        from thursday_security.keychain import KeychainError

        if self._keychain.available:
            try:
                stored = self._keychain.get(KEY_ACCOUNT)
                if stored is not None:
                    self._key = PrivateKey.from_pem(stored)
                    return self._key
                self._key = self._adopt_or_generate()
                return self._key
            except KeychainError as exc:
                # Refuse rather than quietly writing the key to a file. A node that silently
                # downgraded its own key storage would leave the owner believing the keychain
                # protects an identity it never held.
                raise SystemExit(
                    f"this machine has a keychain and Thursday could not use it: {exc}\n"
                    "Fix the keychain, or run with --key-storage=file to accept a 0600 file."
                ) from exc

        return self._from_file()

    def _adopt_or_generate(self) -> PrivateKey:
        """Move an existing file key into the keychain, or make a new one there.

        The order matters and is the whole of the migration: write to the keychain, read it
        back, and only then remove the file. A delete that happened first would lose the
        node's identity to a keychain write that failed — and a device that loses its key has
        to be re-paired by a person standing at it.
        """
        key = PrivateKey.from_pem(self.key_path.read_text()) if self.key_path.exists() else None
        moving = key is not None
        key = key or PrivateKey.generate()

        self._keychain.put(KEY_ACCOUNT, key.to_pem())
        if self._keychain.get(KEY_ACCOUNT) != key.to_pem():
            raise KeyMigrationError(
                "the keychain accepted this node's key and did not hand it back; "
                "leaving the existing key file alone"
            )

        if moving:
            self.key_path.unlink(missing_ok=True)
            log.info("node_key_moved_to_keychain", fingerprint=key.public.fingerprint)
        else:
            log.info(
                "node_key_created", storage=self._keychain.name, fingerprint=key.public.fingerprint
            )
        return key

    def _from_file(self) -> PrivateKey:
        """The fallback. Written 0600 before anything is put in it, not after: a key file
        that exists world-readable for the duration of one write is a key file that leaked."""
        if self.key_path.exists():
            self._key = PrivateKey.from_pem(self.key_path.read_text())
            return self._key

        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        self.key_path.touch(mode=0o600, exist_ok=True)
        self.key_path.chmod(0o600)
        key = PrivateKey.generate()
        self.key_path.write_text(key.to_pem())
        self._key = key
        log.info(
            "node_key_created",
            path=str(self.key_path),
            storage="file",
            fingerprint=key.public.fingerprint,
        )
        return key

    # ------------------------------------------------------------------ rotation (§117)

    @property
    def pending_key_path(self) -> Path:
        return self.path.with_suffix(".pending.key")

    def pending_key(self) -> PrivateKey | None:
        """The key staged for a rotation that has not been confirmed, if there is one."""
        from thursday_security.keychain import KeychainError

        if self._keychain.available:
            with contextlib.suppress(KeychainError):
                stored = self._keychain.get(PENDING_KEY_ACCOUNT)
                if stored is not None:
                    return PrivateKey.from_pem(stored)
        if self.pending_key_path.exists():
            return PrivateKey.from_pem(self.pending_key_path.read_text())
        return None

    def stage_pending(self) -> PrivateKey:
        """Generate the successor key and write it down *before* asking the core to take it.

        Persisted first, deliberately. If the core accepts a rotation and the node dies
        before it hears back, the machine's identity is now a key it never saved — and a
        node whose key the core does not recognise has to be re-paired by a person standing
        at it. Writing first turns that from a lost identity into a resumable one: the key
        is on disk, and `--rotate-key` can ask the core whether it already took it.

        An existing staged key is reused rather than replaced, for the same reason.
        """
        existing = self.pending_key()
        if existing is not None:
            return existing

        key = PrivateKey.generate()
        from thursday_security.keychain import KeychainError

        if self._keychain.available:
            try:
                self._keychain.put(PENDING_KEY_ACCOUNT, key.to_pem())
                return key
            except KeychainError as exc:
                raise SystemExit(
                    f"this machine has a keychain and Thursday could not use it: {exc}"
                ) from exc

        self.pending_key_path.parent.mkdir(parents=True, exist_ok=True)
        self.pending_key_path.touch(mode=0o600, exist_ok=True)
        self.pending_key_path.chmod(0o600)
        self.pending_key_path.write_text(key.to_pem())
        return key

    def promote_pending(self) -> PrivateKey:
        """Make the staged key this node's identity, once the core has accepted it."""
        key = self.pending_key()
        if key is None:
            raise KeyMigrationError("there is no staged key to promote")

        from thursday_security.keychain import KeychainError

        if self._keychain.available:
            self._keychain.put(KEY_ACCOUNT, key.to_pem())
            if self._keychain.get(KEY_ACCOUNT) != key.to_pem():
                # Same rule as the migration in `_adopt_or_generate`: read it back before
                # discarding what it replaces.
                raise KeyMigrationError(
                    "the keychain accepted the rotated key and did not hand it back; "
                    "the staged key has been left in place"
                )
            with contextlib.suppress(KeychainError):
                self._keychain.delete(PENDING_KEY_ACCOUNT)
        else:
            self.key_path.touch(mode=0o600, exist_ok=True)
            self.key_path.chmod(0o600)
            self.key_path.write_text(key.to_pem())
            self.pending_key_path.unlink(missing_ok=True)

        self._key = key
        if pairing := self.data.get("pairing"):
            pairing["fingerprint"] = key.public.fingerprint
            pairing["rotated_at"] = datetime.now(UTC).isoformat()
            self._write(self.data)
        log.info("node_key_rotated", fingerprint=key.public.fingerprint)
        return key

    def discard_pending(self) -> None:
        from thursday_security.keychain import KeychainError

        if self._keychain.available:
            with contextlib.suppress(KeychainError):
                self._keychain.delete(PENDING_KEY_ACCOUNT)
        self.pending_key_path.unlink(missing_ok=True)

    @property
    def storage(self) -> str:
        """Where this node's key actually lives. Reported, never inferred."""
        if self._keychain.available:
            return self._keychain.name
        return "file"

    @property
    def fingerprint(self) -> str:
        return self.key.public.fingerprint

    # ------------------------------------------------------------------ pairing state

    @property
    def paired(self) -> bool:
        """Whether this node has a pairing to sign with.

        True from the moment ``--pair`` succeeds, which is *before* the owner has typed the
        code. That is intentional: the core has the public key on file from that moment, so
        signing with it is the correct thing to attempt, and until the owner confirms, the
        connection is simply refused and retried. The alternative — falling back to the
        token while waiting — would leave the weaker credential live during the exact window
        pairing exists to close.
        """
        return bool(self.data.get("pairing"))

    @property
    def device_id(self) -> uuid.UUID:
        """The id the core knows this node by.

        The core assigns it at pairing rather than accepting the node's own. A node that
        could name itself could name itself as the *server*, and the owner confirming a code
        shown on their laptop would be registering an attacker's key against a machine they
        never touched.
        """
        pairing = self.data.get("pairing") or {}
        return uuid.UUID(pairing.get("device_id") or self.data["device_id"])

    def record_pairing(
        self, *, device_id: str, fingerprint: str, core: str = "", pin: str = ""
    ) -> None:
        self.data["pairing"] = {
            "device_id": device_id,
            "fingerprint": fingerprint,
            "core": core,
            #: The core's SPKI pin, learned during pairing — the one moment a person is
            #: standing at this device confirming what it is talking to (ADR 0041).
            "pin": pin,
            "started_at": datetime.now(UTC).isoformat(),
        }
        self._write(self.data)
        log.info("node_pairing_recorded", device_id=device_id, fingerprint=fingerprint)

    @property
    def core_pin(self) -> Pin | None:
        """The core's key this node agreed to trust, if it recorded one."""
        pairing = self.data.get("pairing") or {}
        value = pairing.get("pin") or ""
        return Pin(value=value, host=pairing.get("core", "")) if value else None

    def forget_pairing(self) -> None:
        """Drop the pairing record after the core has revoked or lost this device.

        The key file stays. Re-pairing generates a fresh request against the same key, which
        keeps the fingerprint the owner already wrote down meaningful.
        """
        if self.data.pop("pairing", None) is not None:
            self._write(self.data)


def api_base(core_url: str) -> str:
    """The REST base that corresponds to a TNP/1 socket URL.

    Derived rather than asked for separately: two URLs for the same core is two things to
    get out of step, and a node pairing with one core and connecting to another would fail
    in a way nobody would read correctly.
    """
    parts = urlsplit(core_url)
    scheme = {"ws": "http", "wss": "https"}.get(parts.scheme, parts.scheme)
    path = parts.path.rstrip("/")
    if path.endswith("/device"):
        path = path[: -len("/device")]
    return urlunsplit((scheme, parts.netloc, path or "/api/v1", "", ""))


def pairing_request(identity: NodeIdentity, *, name: str, os_name: str, hostname: str) -> dict:
    """The body of a `POST /devices/pair/start`, signed with this node's key.

    The signature is over the public key itself among other fields, so the request proves
    the sender holds the private half of exactly the key it is asking the core to trust.
    Only the public half is in the body; there is no code path here that reads `to_pem`.
    """
    nonce = secrets.token_hex(16)
    issued_at = datetime.now(UTC)
    public_key = identity.key.public.encoded
    signature = identity.key.sign(
        pairing_payload(
            public_key=public_key,
            name=name,
            os=os_name,
            hostname=hostname,
            nonce=nonce,
            issued_at=issued_at,
        )
    )
    return {
        "public_key": public_key,
        "name": name,
        "os": os_name,
        "hostname": hostname,
        "nonce": nonce,
        "issued_at": issued_at.isoformat(),
        "signature": signature,
    }


def rotation_request(identity: NodeIdentity, successor: PrivateKey) -> dict:
    """The body of a `POST /devices/{id}/rotate`, signed by both keys.

    The retiring key is the authority — it is what the core already trusts for this device.
    The incoming key signs the same bytes to prove the node can actually use what it is
    asking the core to adopt; without that second signature a typo in this function would
    hand the core a key nobody holds, and the machine would need re-pairing by hand.
    """
    nonce = secrets.token_hex(16)
    issued_at = datetime.now(UTC)
    new_public_key = successor.public.encoded
    payload = rotation_payload(
        device_id=str(identity.device_id),
        old_fingerprint=identity.key.public.fingerprint,
        new_public_key=new_public_key,
        nonce=nonce,
        issued_at=issued_at,
    )
    return {
        "new_public_key": new_public_key,
        "signature_by_old": identity.key.sign(payload),
        "signature_by_new": successor.sign(payload),
        "nonce": nonce,
        "issued_at": issued_at.isoformat(),
    }


def rotate_key(identity: NodeIdentity, *, core_url: str) -> int:
    """Replace this node's key with a fresh one (§117), without a person re-pairing it.

    The interesting case is not the happy path; it is the reply that never arrives. The core
    may have accepted the rotation and the node may never learn it, which would leave the
    machine signing with a key the core has already retired — locked out, and needing
    somebody to walk to it.

    So the successor is written down first, and when the request fails the node *asks the
    core what it holds* rather than assuming. If the core's fingerprint for this device is
    already the staged key, the earlier attempt landed and this promotes it. That turns the
    one failure mode that costs a physical visit into an ordinary retry.
    """
    import httpx

    if not identity.paired:
        print("this node is not paired, so there is no key to rotate; run --pair first")
        return 1

    base = api_base(core_url)
    successor = identity.stage_pending()
    url = f"{base}/devices/{identity.device_id}/rotate"
    try:
        response = httpx.post(url, json=rotation_request(identity, successor), timeout=15.0)
    except httpx.HTTPError as exc:
        print(f"could not reach the core at {base}: {exc}")
        return _resolve_staged(identity, successor, base=base)

    if response.status_code == 200:
        identity.promote_pending()
        print(f"rotated. this node's key is now {identity.key.public.fingerprint}")
        return 0

    detail = response.text.strip()
    print(f"the core refused the rotation ({response.status_code}): {detail}")
    return _resolve_staged(identity, successor, base=base)


def _resolve_staged(identity: NodeIdentity, successor: PrivateKey, *, base: str) -> int:
    """Ask the core whether it already accepted the staged key.

    Read-only, and it decides nothing on its own: it compares the fingerprint the core
    reports for this device with the one the node staged. Equal means an earlier attempt
    succeeded and only the answer was lost.
    """
    import httpx

    try:
        response = httpx.get(f"{base}/devices/credentials", timeout=15.0)
        rows = response.json().get("credentials", []) if response.status_code == 200 else []
    except (httpx.HTTPError, ValueError) as exc:
        print(f"could not ask the core which key it holds: {exc}")
        rows = []

    mine = next((r for r in rows if r.get("device_id") == str(identity.device_id)), None)
    if mine and mine.get("fingerprint") == successor.public.fingerprint:
        identity.promote_pending()
        print(
            "the core had already accepted this key — the reply was lost, not the rotation.\n"
            f"this node's key is now {identity.key.public.fingerprint}"
        )
        return 0

    print(
        "the staged key has been kept. run --rotate-key again to retry;"
        " this node is still using its current key and still works."
    )
    return 1


def pair(identity: NodeIdentity, *, core_url: str, name: str, os_name: str) -> int:
    """Ask the core to pair this node, and print the code for the owner to confirm.

    Prints the key fingerprint next to the code. The code proves somebody is standing at
    this machine; the fingerprint is what lets the owner check that the device they confirm
    in the app is the one in front of them, rather than a second request that arrived in the
    same five minutes.
    """
    import httpx

    base = api_base(core_url)
    body = pairing_request(identity, name=name, os_name=os_name, hostname=platform.node())
    try:
        response = httpx.post(f"{base}/devices/pair/start", json=body, timeout=15.0)
    except httpx.HTTPError as exc:
        print(f"could not reach the core at {base}: {exc}")
        return 1
    if response.status_code != 200:
        detail = response.json().get("detail", response.text)
        print(f"the core refused the pairing request: {detail}")
        return 1

    reply = response.json()

    # Learn what key the core actually has, now, while a person is here to confirm the code.
    # Trust-on-first-use is only as good as the moment it happens, and this is the one moment
    # in the whole system where somebody is standing at the device (ADR 0041).
    pin = ""
    if urlsplit(core_url).scheme in {"wss", "https"}:
        try:
            pin = peer_pin(base)
        except PinUnavailable as exc:
            print(f"could not read the core's certificate, so no pin was recorded: {exc}")

    identity.record_pairing(
        device_id=reply["device_id"],
        fingerprint=identity.fingerprint,
        core=core_url,
        pin=pin,
    )
    print(
        f"\n  pairing code   {reply['pairing_code']}\n"
        f"  key            {identity.fingerprint}\n"
        f"  expires        {reply['expires_at']}\n"
        + (f"  core key       {Pin(value=pin).short}\n" if pin else "")
        + "\n"
        "Confirm this code in Thursday, checking the key matches. Then start the node\n"
        "normally — it will sign with its own key from now on, and no longer needs\n"
        f"{TOKEN_ENV} set on this machine.\n"
    )
    return 0


class NodeClient:
    def __init__(
        self,
        *,
        core_url: str,
        name: str,
        identity: NodeIdentity,
        executor: NodeExecutor,
        token: str,
        kind: str = "desktop",
        heartbeat_s: float = 15.0,
        compute: LocalModelManager | None = None,
    ) -> None:
        self.core_url = core_url
        self.name = name
        self.identity = identity
        self.executor = executor
        #: The enrolment token, from the environment. Held only in memory, never logged, and
        #: unused once this node has paired.
        self.token = token
        self.kind = kind
        self.heartbeat_s = heartbeat_s
        #: This machine's AI inventory (ADDENDUM §41). Constructed here rather than injected
        #: because every node has one — a machine with no runtime reports an empty inventory,
        #: which is an answer the core needs, not a reason to leave the field unset.
        self.compute = compute or LocalModelManager()
        self._running: dict[uuid.UUID, asyncio.Task] = {}
        #: Read by the diagnostics endpoint. The point of that endpoint is to answer
        #: "why is nothing happening", so the reason a connection failed is kept.
        self.connected = False
        self.last_error: str | None = None

    async def run_forever(self) -> None:
        delay = RECONNECT_BASE_S
        while True:
            started = time.monotonic()
            try:
                await self._session()
                delay = RECONNECT_BASE_S
            except websockets.ConnectionClosed as exc:
                self.connected = False
                lasted = time.monotonic() - started
                # A session that reached its maximum age (§79) is not a failure. Backing off
                # from it — and logging it as a disconnection — would make the ordinary
                # consequence of a security control look like a fault, and would leave the
                # machine unreachable for the length of the backoff every time.
                if _close_code(exc) == CLOSE_SESSION_EXPIRED and lasted >= MIN_HEALTHY_SESSION_S:
                    log.info("node_session_expired", lasted_s=round(lasted), action="reconnecting")
                    self.last_error = None
                    delay = RECONNECT_BASE_S
                    continue
                # The `lasted` guard is why this is not simply "expired means reconnect": a
                # core that expires sessions the instant they open would otherwise have every
                # node reconnecting in a tight loop, which is a denial of service the nodes
                # perform on their owner's behalf.
                delay = await self._back_off(exc, delay)
            except (OSError, websockets.WebSocketException) as exc:
                self.connected = False
                delay = await self._back_off(exc, delay)
            except asyncio.CancelledError:
                raise

    async def _back_off(self, exc: Exception, delay: float) -> float:
        self.last_error = f"{type(exc).__name__}: {exc}"
        log.warning("node_disconnected", error=str(exc), retry_in=round(delay, 1))
        await asyncio.sleep(delay)
        return min(delay * 2, RECONNECT_MAX_S)

    def sign_hello(self, hello: Hello) -> str:
        """Sign the HELLO with whichever identity this node actually has.

        A paired node signs with its own key and never falls back to the token, even when the
        key is refused. Falling back would mean pairing bought nothing: anyone holding the
        shared enrolment token could still connect as this machine, which is the exact
        weakness §80 exists to remove. The core enforces the same rule from its side.
        """
        if self.identity.paired:
            return self.identity.key.sign(
                hello_payload(
                    device_id=str(hello.device_id),
                    name=hello.name,
                    os=hello.os,
                    nonce=hello.nonce,
                    issued_at=hello.ts,
                )
            )
        # Sign the frame's own fields and its own timestamp, so the core can tell this
        # HELLO from a replay of one it saw earlier under a different name.
        return sign(
            self.token,
            signing_payload(
                device_id=str(hello.device_id),
                name=hello.name,
                os=hello.os,
                nonce=hello.nonce,
                issued_at=hello.ts,
            ),
        )

    async def _session(self) -> None:
        pin = self.identity.core_pin
        connect_args: dict[str, Any] = {"max_size": 32 * 1024 * 1024}
        if pin is not None:
            # Trust comes from the pin rather than from a CA, which is the whole point: the
            # public CA set is exactly what an attacker with a mis-issued certificate has.
            connect_args["ssl"] = pinned_context()

        async with websockets.connect(self.core_url, **connect_args) as ws:
            if pin is not None:
                # Checked before a single frame is sent. A HELLO handed to an impostor is a
                # HELLO an impostor can relay, and the node's own key does not help: it
                # authenticates this node *to* whoever is listening.
                check_peer(_ssl_object(ws), pin)
            nonce = secrets.token_hex(16)
            # ADDENDUM §3–§5. Re-read on every connect rather than cached at startup: a
            # node that has been running for a week and had a model installed yesterday
            # would otherwise keep telling the core it has nothing.
            models = await self.compute.refresh()
            hello = Hello(
                device_id=self.identity.device_id,
                name=self.name,
                kind=self.kind,
                os=self.executor.adapter.os_name,
                os_version=platform.version(),
                capabilities=self.executor.adapter.capabilities().grant(
                    *self.compute.capabilities()
                ),
                telemetry=await self.executor.adapter.telemetry(),
                compute=self.compute.profile(),
                models=models,
                nonce=nonce,
            )
            hello.signature = self.sign_hello(hello)
            await ws.send(hello.model_dump_json())

            reply = parse_frame(await ws.recv())
            if not isinstance(reply, Welcome):
                # Most often an ERROR frame saying the signature did not check out. Say so
                # in terms the person running the node can act on.
                detail = getattr(reply, "message", str(reply))
                self.connected = False
                self.last_error = f"core refused the connection: {detail}"
                if self.identity.paired:
                    # By far the most likely cause, and one the operator can fix by walking
                    # to the machine rather than by reading a signature error.
                    self.last_error += (
                        " — this node has paired but the code may not have been confirmed"
                        " yet, or its identity has been revoked"
                    )
                raise RuntimeError(self.last_error)
            self.connected = True
            self.last_error = None
            log.info(
                "node_connected",
                core=self.core_url,
                name=self.name,
                identity="key" if self.identity.paired else "enrolment token",
            )

            heartbeat = asyncio.create_task(self._heartbeat(ws))
            try:
                async for raw in ws:
                    frame = parse_frame(raw)
                    if isinstance(frame, ActionFrame):
                        # Actions run concurrently so one slow action cannot block the socket.
                        task = asyncio.create_task(self._handle(ws, frame))
                        self._running[frame.action_id] = task
                        task.add_done_callback(
                            lambda _, a=frame.action_id: self._running.pop(a, None)
                        )
                    elif isinstance(frame, ShutdownFrame):
                        log.info("node_shutdown_requested", reason=frame.reason)
                        return
            finally:
                self.connected = False
                heartbeat.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat

    async def _heartbeat(self, ws) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_s)
            telemetry = await self.executor.adapter.telemetry()
            await ws.send(
                Heartbeat(telemetry=telemetry, load=self.compute.load()).model_dump_json()
            )

    async def _handle(self, ws, frame: ActionFrame) -> None:
        result = await self.executor.execute(
            DeviceAction(
                id=frame.action_id,
                action=frame.action,
                args=frame.args,
                timeout_s=frame.timeout_s,
                trace_id=frame.trace_id,
            )
        )
        await ws.send(
            ActionResultFrame(
                action_id=frame.action_id,
                ok=result.ok,
                verified=result.verified,
                evidence=result.evidence,
                data=result.data,
                error=result.error,
                duration_ms=result.duration_ms,
                undo=result.undo,
            ).model_dump_json()
        )


def main() -> None:
    parser = argparse.ArgumentParser(prog="thursday-node", description="Thursday device node")
    parser.add_argument("--name", default=platform.node(), help="device name the owner will speak")
    parser.add_argument("--core", default="ws://127.0.0.1:8000/api/v1/device")
    parser.add_argument(
        "--kind", default="desktop", choices=["desktop", "laptop", "server", "phone"]
    )
    parser.add_argument(
        "--pair",
        action="store_true",
        help="register this node's key with the core and print a code to confirm, then exit",
    )
    parser.add_argument(
        "--rotate-key",
        action="store_true",
        help="replace this node's key with a fresh one and exit (no re-pairing needed)",
    )
    parser.add_argument(
        "--forget-pairing",
        action="store_true",
        help="drop this node's pairing record (after the owner revoked it) and exit",
    )
    parser.add_argument(
        "--allow-root",
        action="append",
        default=None,
        help="a directory this node may touch (repeatable). Defaults to the home directory.",
    )
    parser.add_argument("--key-file", default=str(Path.home() / ".thursday" / "node.json"))
    parser.add_argument(
        "--diagnostics-port",
        type=int,
        default=int(os.environ.get("THURSDAY_NODE_PORT", "8765")),
        help="loopback port for GET /health and GET /capabilities (0 disables)",
    )
    parser.add_argument(
        "--diagnostics-host", default=os.environ.get("THURSDAY_NODE_HOST", "127.0.0.1")
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    configure_logging(level=args.log_level)
    identity = NodeIdentity(Path(args.key_file))

    if args.forget_pairing:
        identity.forget_pairing()
        print("pairing record dropped; this node will use the enrolment token again")
        raise SystemExit(0)

    if args.rotate_key:
        # After `--forget-pairing`, not before. If somebody passes both, the one that means
        # "this identity is finished" has to win — rotating a pairing record that is about
        # to be dropped would ask the core to adopt a key for a device this node is in the
        # middle of forgetting.
        raise SystemExit(rotate_key(identity, core_url=args.core))

    roots = [Path(p) for p in (args.allow_root or [str(Path.home())])]
    executor = NodeExecutor(for_current_platform(), allowed_roots=roots)

    if args.pair:
        raise SystemExit(
            pair(
                identity,
                core_url=args.core,
                name=args.name,
                os_name=executor.adapter.os_name,
            )
        )

    # From the environment, never from a flag: a token on the command line lands in the
    # shell history and in every `ps` listing on the machine. A paired node does not need
    # it at all, which is the practical benefit of pairing and worth not undermining by
    # demanding the token anyway.
    token = os.environ.get(TOKEN_ENV, "")
    if not token and not identity.paired:
        raise SystemExit(
            f"{TOKEN_ENV} is not set and this node has not paired. Either pair it —\n"
            f"  python -m apps.node --pair --name {args.name} --core {args.core}\n"
            f"— or set the same token on both sides, e.g.\n"
            f"  export {TOKEN_ENV}=$(python -c 'import secrets;print(secrets.token_urlsafe(32))')"
        )

    client = NodeClient(
        core_url=args.core,
        name=args.name,
        identity=identity,
        executor=executor,
        token=token,
        kind=args.kind,
    )
    log.info(
        "node_starting",
        name=args.name,
        os=executor.adapter.os_name,
        roots=[str(r) for r in roots],
        identity="key" if identity.paired else "enrolment token",
    )

    async def run() -> None:
        tasks = [asyncio.create_task(client.run_forever())]
        if args.diagnostics_port:
            tasks.append(
                asyncio.create_task(
                    serve_diagnostics(
                        client, host=args.diagnostics_host, port=args.diagnostics_port
                    )
                )
            )
            log.info(
                "node_diagnostics_listening",
                url=f"http://{args.diagnostics_host}:{args.diagnostics_port}/health",
            )
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run())
    log.info("node_stopped")


if __name__ == "__main__":
    main()


def _ssl_object(ws: Any) -> Any:
    """The live TLS object behind a websocket, or None when the connection is plaintext.

    Reached through the transport because `websockets` does not surface it directly. Returning
    None rather than raising is deliberate: "there is no TLS here" is a fact `check_peer` needs
    to see, and it treats it as a mismatch rather than as a missing feature.
    """
    transport = getattr(ws, "transport", None)
    return transport.get_extra_info("ssl_object") if transport is not None else None


def _close_code(exc: websockets.ConnectionClosed) -> int | None:
    """The code the *core* sent, or None if it never sent one.

    Only `rcvd` is consulted. `sent` is this node's own close frame, and reading it would
    make the node's reason for hanging up look like the core's — so a node that closed a
    connection itself could conclude the core had expired its session and reconnect at once
    in a loop.
    """
    received = getattr(exc, "rcvd", None)
    return getattr(received, "code", None) if received is not None else None
