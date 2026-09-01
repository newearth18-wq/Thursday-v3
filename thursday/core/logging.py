"""Structured logging (§82).

Every log line carries ``trace_id`` so one user utterance can be reconstructed across the
core, the agents it delegated to, and the devices it touched. Secrets are redacted by a
processor, so no call site has to remember to do it.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from thursday.security.redaction import SecretRedactor
from thursday.shared.ids import current_trace_id

_redactor = SecretRedactor()


def _add_trace_id(_logger: Any, _name: str, event: dict[str, Any]) -> dict[str, Any]:
    event.setdefault("trace_id", current_trace_id())
    return event


def _redact(_logger: Any, _name: str, event: dict[str, Any]) -> dict[str, Any]:
    for key, value in event.items():
        if isinstance(value, str):
            result = _redactor.redact(value)
            if not result.clean:
                event[key] = result.text
    return event


def configure_logging(*, level: str = "INFO", json_output: bool = False) -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=getattr(logging, level.upper(), logging.INFO))
    renderer = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _add_trace_id,
            _redact,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[return-value]
