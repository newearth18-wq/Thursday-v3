"""The node's own local HTTP surface.

Read-only, bound to loopback, and entirely separate from the command channel. Commands
arrive over the outbound WebSocket (ADR 0015) — this exists for the moment when they are
*not* arriving, and someone standing at the machine needs to know whether the node is
running, what it thinks it can do, and why it cannot reach the core. Answering that over
the channel that is broken would be no use, which is why this is a second one.

It executes nothing. The worst it discloses to someone already on the machine is what the
process list would have told them anyway.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI


def diagnostics_app(client: Any) -> FastAPI:
    app = FastAPI(title="Thursday node", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    async def health() -> dict:
        return {
            "ok": True,
            "device_id": str(client.identity.device_id),
            "name": client.name,
            "os": client.executor.adapter.os_name,
            "connected_to_core": client.connected,
            "core_url": client.core_url,
            # Why the socket is down, in the node's own words. Never the token.
            "last_error": client.last_error,
            "allowed_roots": [str(root) for root in client.executor.allowed_roots],
            "now": datetime.now(UTC).isoformat(),
        }

    @app.get("/capabilities")
    async def capabilities() -> dict:
        """What this node will actually do, read from the executor's dispatch table.

        Read rather than hand-listed: a capabilities endpoint that drifts from what the
        node implements is worse than none, because it is believed.
        """
        return {
            "device_id": str(client.identity.device_id),
            "advertised": sorted(client.executor.adapter.capabilities().granted),
            "implemented": sorted(client.executor.supported_actions()),
        }

    return app


async def serve_diagnostics(client: Any, *, host: str = "127.0.0.1", port: int = 8765) -> None:
    """Run the diagnostics server until cancelled.

    Loopback by default. A node listening on 0.0.0.0 announces to the whole network what
    software runs on this machine and which directories it may touch.
    """
    import uvicorn

    config = uvicorn.Config(
        diagnostics_app(client), host=host, port=port, log_level="warning", access_log=False
    )
    server = uvicorn.Server(config)
    try:
        await server.serve()
    except asyncio.CancelledError:
        server.should_exit = True
        raise
