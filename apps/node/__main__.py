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
import platform
import secrets
import uuid
from pathlib import Path

import websockets
from thursday_core.logging import configure_logging, get_logger
from thursday_devices.node.adapters import for_current_platform
from thursday_devices.node.executor import NodeExecutor
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

log = get_logger("thursday.node")

RECONNECT_BASE_S = 2.0
RECONNECT_MAX_S = 60.0


class NodeIdentity:
    """Stable device id plus a keypair placeholder, persisted between runs."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            return json.loads(self.path.read_text())
        identity = {"device_id": str(uuid.uuid4()), "secret": secrets.token_hex(32)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(identity, indent=2))
        self.path.chmod(0o600)
        log.info("node_identity_created", path=str(self.path))
        return identity

    @property
    def device_id(self) -> uuid.UUID:
        return uuid.UUID(self.data["device_id"])

    def sign(self, nonce: str) -> str:
        """Placeholder for the Ed25519 signature the core verifies at HELLO (§9.1)."""
        import hashlib

        return hashlib.sha256(f"{self.data['secret']}{nonce}{self.device_id}".encode()).hexdigest()


class NodeClient:
    def __init__(
        self,
        *,
        core_url: str,
        name: str,
        identity: NodeIdentity,
        executor: NodeExecutor,
        kind: str = "desktop",
        heartbeat_s: float = 15.0,
    ) -> None:
        self.core_url = core_url
        self.name = name
        self.identity = identity
        self.executor = executor
        self.kind = kind
        self.heartbeat_s = heartbeat_s
        self._running: dict[uuid.UUID, asyncio.Task] = {}

    async def run_forever(self) -> None:
        delay = RECONNECT_BASE_S
        while True:
            try:
                await self._session()
                delay = RECONNECT_BASE_S
            except (OSError, websockets.WebSocketException) as exc:
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
                signature=self.identity.sign(nonce),
            )
            await ws.send(hello.model_dump_json())

            welcome = parse_frame(await ws.recv())
            if not isinstance(welcome, Welcome):
                raise RuntimeError(f"core refused the connection: {welcome}")
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
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    configure_logging(level=args.log_level)
    roots = [Path(p) for p in (args.allow_root or [str(Path.home())])]
    executor = NodeExecutor(for_current_platform(), allowed_roots=roots)
    client = NodeClient(
        core_url=args.core,
        name=args.name,
        identity=NodeIdentity(Path(args.key_file)),
        executor=executor,
        kind=args.kind,
    )
    log.info(
        "node_starting",
        name=args.name,
        os=executor.adapter.os_name,
        roots=[str(r) for r in roots],
    )
    try:
        asyncio.run(client.run_forever())
    except KeyboardInterrupt:
        log.info("node_stopped")


if __name__ == "__main__":
    main()
