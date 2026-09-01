"""Application state and dependencies."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from thursday.core.container import Container


def get_container(request: Request) -> Container:
    container: Container | None = getattr(request.app.state, "container", None)
    if container is None:  # pragma: no cover - set during app startup
        raise RuntimeError("the container is not attached to this application")
    return container


def error_body(exc: Any, trace_id: str) -> dict[str, Any]:
    code = getattr(exc, "code", "internal_error")
    return {
        "error": {
            "code": code,
            "message": getattr(exc, "message", str(exc)),
            "details": getattr(exc, "details", {}),
            "trace_id": trace_id,
        }
    }
