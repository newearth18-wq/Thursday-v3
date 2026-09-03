"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from thursday_core.config import Settings, get_settings
from thursday_core.container import Container, build_container, start
from thursday_core.logging import get_logger
from thursday_realtime.gateway import router as realtime_router
from thursday_shared import __version__
from thursday_shared.errors import (
    ApprovalRequired,
    PermissionDenied,
    PrivacyViolation,
    ThursdayError,
)
from thursday_shared.ids import bind_trace_id, current_trace_id

from thursday_api.deps import error_body
from thursday_api.routers import (
    approvals,
    conversation,
    devices,
    memory,
    projects,
    skills,
    system,
)

log = get_logger(__name__)

_STATUS_BY_ERROR = {
    PermissionDenied: 403,
    PrivacyViolation: 403,
    ApprovalRequired: 202,
}


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.container = container or build_container(settings)
        # Loading what was kept is async, so it happens here rather than in `build_container`
        # (Sprint 51). Skipping it gives exactly the pre-persistence behaviour, which is why
        # every test that builds a container directly still works.
        await start(app.state.container)
        log.info("thursday_started", version=__version__, environment=settings.environment)
        yield
        log.info("thursday_stopping")

    app = FastAPI(
        title="Thursday",
        description="Personal AI operating system — one assistant, many capabilities.",
        version=__version__,
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def trace_middleware(request: Request, call_next):
        trace_id = bind_trace_id(request.headers.get("x-trace-id"))
        response = await call_next(request)
        response.headers["x-trace-id"] = trace_id
        return response

    @app.exception_handler(ThursdayError)
    async def thursday_error_handler(request: Request, exc: ThursdayError) -> JSONResponse:
        status = next(
            (code for kind, code in _STATUS_BY_ERROR.items() if isinstance(exc, kind)), 400
        )
        return JSONResponse(status_code=status, content=error_body(exc, current_trace_id()))

    api_prefix = "/api/v1"
    for router in (
        conversation.router,
        tasks_router(),
        devices.router,
        memory.router,
        approvals.router,
        projects.router,
        skills.router,
        system.router,
    ):
        app.include_router(router, prefix=api_prefix)
    app.include_router(realtime_router, prefix=api_prefix)

    @app.get("/")
    async def root() -> dict:
        return {"name": "Thursday", "version": __version__, "docs": "/docs"}

    return app


def tasks_router():
    from thursday_api.routers import tasks

    return tasks.router
