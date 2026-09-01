"""Identifier helpers.

Every object that crosses a process boundary carries a UUID, and every unit of work
carries a ``trace_id`` so multi-agent execution can be reconstructed from logs (§82).
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar

_trace_id: ContextVar[str | None] = ContextVar("thursday_trace_id", default=None)


def new_id() -> uuid.UUID:
    return uuid.uuid4()


def new_trace_id() -> str:
    return uuid.uuid4().hex[:16]


def current_trace_id() -> str:
    """The trace id for the work in flight, minting one if this is the root."""
    value = _trace_id.get()
    if value is None:
        value = new_trace_id()
        _trace_id.set(value)
    return value


def bind_trace_id(trace_id: str | None = None) -> str:
    """Bind a trace id to the current context and return it."""
    resolved = trace_id or new_trace_id()
    _trace_id.set(resolved)
    return resolved


def reset_trace_id() -> None:
    _trace_id.set(None)
