"""Thursday device node.

Runs on the user's machine and dials *out* to the core, so nothing has to listen on a port
and no inbound firewall rule is needed.

    python -m apps.node --name Office-PC --core ws://127.0.0.1:8000/api/v1/device

The node's identity key is generated on first run and stored beside its config. In
production it belongs in the OS keychain; the ``--key-file`` fallback exists so the node
runs on a machine without one, and says so.
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
from pathlib import Path

import websockets
from thursday_core.logging import configure_logging, get_logger
from thursday_devices.node.adapters import for_current_platform
from thursday_devices.node.executor import NodeExecutor
from thursday_security.device_auth import sign, signing_payload
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
    """The node's stable device id, persisted between runs.

    Only the id lives here. The enrolment token comes from the environment and is never
    written to this file: a stolen laptop should yield a device id, not a credential that
    lets the thief register a second machine as the owner's.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data = self._load()

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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(identity, indent=2))
        self.path.chmod(0o600)
        log.info("node_identity_created", path=str(self.path))
        return identity

    @property
    def device_id(self) -> uuid.UUID:
        return uuid.UUID(self.data["device_id"])


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
        #: The enrolment token, from the environment. Held only in memory and never logged.
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
            # Sign the frame's own fields and its own timestamp, so the core can tell this
            # HELLO from a replay of one it saw earlier under a different name.
            hello.signature = sign(
                self.token,
                signing_payload(
                    device_id=str(hello.device_id),
                    name=hello.name,
                    os=hello.os,
                    nonce=hello.nonce,
                    issued_at=hello.ts,
                ),
            )
            await ws.send(hello.model_dump_json())

            reply = parse_frame(await ws.recv())
            if not isinstance(reply, Welcome):
                # Most often an ERROR frame saying the signature did not check out. Say so
                # in terms the person running the node can act on.
                detail = getattr(reply, "message", str(reply))
                self.connected = False
                self.last_error = f"core refused the connection: {detail}"
                raise RuntimeError(self.last_error)
            self.connected = True
            self.last_error = None
            log.info("node_connected", core=self.core_url, name=self.name)

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

    # From the environment, never from a flag: a token on the command line lands in the
    # shell history and in every `ps` listing on the machine.
    token = os.environ.get(TOKEN_ENV, "")
    if not token:
        raise SystemExit(
            f"{TOKEN_ENV} is not set. The core will refuse an unsigned HELLO.\n"
            f"Set the same value on both sides, e.g.\n"
            f"  export {TOKEN_ENV}=$(python -c 'import secrets;print(secrets.token_urlsafe(32))')"
        )

    roots = [Path(p) for p in (args.allow_root or [str(Path.home())])]
    executor = NodeExecutor(for_current_platform(), allowed_roots=roots)
    client = NodeClient(
        core_url=args.core,
        name=args.name,
        identity=NodeIdentity(Path(args.key_file)),
        executor=executor,
        token=token,
        kind=args.kind,
    )
    log.info(
        "node_starting",
        name=args.name,
        os=executor.adapter.os_name,
        roots=[str(r) for r in roots],
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
