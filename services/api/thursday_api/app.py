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
    RateLimited,
    ThursdayError,
)
from thursday_shared.ids import bind_trace_id, current_trace_id

from thursday_api.deps import error_body
from thursday_api.limits import (
    Limit,
    RateLimiter,
    caller_of,
    classify,
)
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

API_PREFIX = "/api/v1"

#: POST here can reach a model, an agent or a device, and therefore costs real money or
#: real time. Prefixes rather than exact paths so a new route under one of them is
#: limited by default — the safer direction for a list somebody will forget to update.
EXPENSIVE_PREFIXES = (
    f"{API_PREFIX}/conversations",
    f"{API_PREFIX}/skills",
    f"{API_PREFIX}/automations",
    f"{API_PREFIX}/tasks",
    f"{API_PREFIX}/memory",
    f"{API_PREFIX}/devices",
)

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

    limiter = RateLimiter(
        {
            "default": Limit(settings.rate_limit_default_per_minute, 60.0),
            "expensive": Limit(settings.rate_limit_expensive_per_minute, 60.0),
            "approvals": Limit(settings.rate_limit_approvals_per_minute, 60.0),
            "pairing": Limit(settings.rate_limit_pairing_per_minute, 60.0),
        }
    )
    app.state.limiter = limiter
    trusted = frozenset(settings.trusted_proxies)

    # Registered *before* `trace_middleware`, which makes it the inner one: Starlette builds
    # its stack so the last middleware registered ends up outermost. The first version of
    # this had them the other way round on the strength of a comment saying the opposite, and
    # the 429 came back with no `x-trace-id` and a freshly minted trace id in its body — an
    # error response nobody could correlate with the request that caused it, which is most of
    # what an error response is for.
    @app.middleware("http")
    async def rate_limit_middleware(request: Request, call_next):
        klass = classify(
            request.url.path,
            request.method,
            expensive=EXPENSIVE_PREFIXES,
            approvals=f"{API_PREFIX}/approvals",
        )
        if klass is None:
            return await call_next(request)

        caller = caller_of(
            getattr(request.client, "host", None),
            request.headers.get("x-forwarded-for"),
            trusted,
        )
        decision = limiter.check(caller, klass)
        if decision.allowed:
            return await call_next(request)

        exc = RateLimited(
            f"too many {klass} requests; slow down",
            retry_after_s=decision.retry_after_s,
            limit=klass,
        )
        log.warning("rate_limited", caller=caller, klass=klass, path=request.url.path)
        return JSONResponse(
            status_code=429,
            content=error_body(exc, current_trace_id()),
            # The number a well-behaved client needs. Without it a caller backs off by
            # guessing, and the common guess — retry at once — keeps the limit tripped.
            headers={"Retry-After": str(max(1, round(decision.retry_after_s)))},
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

    api_prefix = API_PREFIX
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
