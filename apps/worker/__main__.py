"""Thursday's background worker.

    python -m apps.worker

Runs the periodic jobs (memory decay, health, device liveness, approval expiry) and, when
Redis is configured, consumes the Dramatiq queue. Without Redis it still runs the periodic
jobs in-process, so a single-process install loses no behaviour — only parallelism.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal

from thursday_core.config import get_settings
from thursday_core.container import build_container
from thursday_core.logging import configure_logging, get_logger
from thursday_worker.jobs import BackgroundWorker, bind_container

log = get_logger("thursday.worker")


async def run(args: argparse.Namespace) -> None:
    settings = get_settings().model_copy(update={"log_level": args.log_level})
    container = build_container(settings)
    bind_container(container)

    worker = BackgroundWorker(container)
    await worker.start()
    log.info(
        "worker_started",
        queue=container.queue.__class__.__name__,
        state=container.state.name,
        redis=bool(settings.redis_url),
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # Windows has no add_signal_handler
            loop.add_signal_handler(sig, stop.set)

    try:
        await stop.wait()
    finally:
        await worker.stop()
        log.info("worker_stopped")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="thursday-worker", description="Thursday background worker"
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    configure_logging(level=args.log_level)
    # Ctrl-C is how an operator stops a worker; a traceback for it is noise, not news.
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run(args))


if __name__ == "__main__":
    main()
