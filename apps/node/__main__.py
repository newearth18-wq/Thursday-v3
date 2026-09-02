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
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import websockets
from thursday_core.logging import configure_logging, get_logger
from thursday_devices.node.adapters import for_current_platform
from thursday_devices.node.executor import NodeExecutor
from thursday_security.device_auth import sign, signing_payload
from thursday_security.keys import PrivateKey, hello_payload, pairing_payload
from thursday_shared.models import DeviceAction
from thursday_shared.protocol import (
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

RECONNECT_BASE_S = 2.0
RECONNECT_MAX_S = 60.0


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

    def __init__(self, path: Path) -> None:
        self.path = path
        self.key_path = path.with_suffix(".key")
        self.data = self._load()
        self._key: PrivateKey | None = None

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

        Written 0600 before anything is put in it, not after: a key file that exists
        world-readable for the duration of one write is a key file that leaked.
        """
        if self._key is not None:
            return self._key
        if self.key_path.exists():
            self._key = PrivateKey.from_pem(self.key_path.read_text())
            return self._key

        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        self.key_path.touch(mode=0o600, exist_ok=True)
        self.key_path.chmod(0o600)
        key = PrivateKey.generate()
        self.key_path.write_text(key.to_pem())
        self._key = key
        log.info("node_key_created", path=str(self.key_path), fingerprint=key.public.fingerprint)
        return key

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

    def record_pairing(self, *, device_id: str, fingerprint: str, core: str = "") -> None:
        self.data["pairing"] = {
            "device_id": device_id,
            "fingerprint": fingerprint,
            "core": core,
            "started_at": datetime.now(UTC).isoformat(),
        }
        self._write(self.data)
        log.info("node_pairing_recorded", device_id=device_id, fingerprint=fingerprint)

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
    identity.record_pairing(
        device_id=reply["device_id"], fingerprint=identity.fingerprint, core=core_url
    )
    print(
        f"\n  pairing code   {reply['pairing_code']}\n"
        f"  key            {identity.fingerprint}\n"
        f"  expires        {reply['expires_at']}\n\n"
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
        self._running: dict[uuid.UUID, asyncio.Task] = {}
        #: Read by the diagnostics endpoint. The point of that endpoint is to answer
        #: "why is nothing happening", so the reason a connection failed is kept.
        self.connected = False
        self.last_error: str | None = None

    async def run_forever(self) -> None:
        delay = RECONNECT_BASE_S
        while True:
            try:
                await self._session()
                delay = RECONNECT_BASE_S
            except (OSError, websockets.WebSocketException) as exc:
                self.connected = False
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.warning("node_disconnected", error=str(exc), retry_in=round(delay, 1))
                await asyncio.sleep(delay)
                delay = min(delay * 2, RECONNECT_MAX_S)
            except asyncio.CancelledError:
                raise

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
        async with websockets.connect(self.core_url, max_size=32 * 1024 * 1024) as ws:
            nonce = secrets.token_hex(16)
            hello = Hello(
                device_id=self.identity.device_id,
                name=self.name,
                kind=self.kind,
                os=self.executor.adapter.os_name,
                os_version=platform.version(),
                capabilities=self.executor.adapter.capabilities(),
                telemetry=await self.executor.adapter.telemetry(),
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
            await ws.send(Heartbeat(telemetry=telemetry).model_dump_json())

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
