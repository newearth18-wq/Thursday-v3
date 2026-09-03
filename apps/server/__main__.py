"""Thursday Core server.

python -m apps.server
"""

from __future__ import annotations

import argparse

import uvicorn
from thursday_api.app import create_app
from thursday_core.config import get_settings


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(prog="thursday-server", description="Thursday Core API")
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    uvicorn.run(
        "apps.server.__main__:app" if args.reload else create_app(settings),
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=settings.log_level.lower(),
        factory=False,
    )


app = create_app()

if __name__ == "__main__":
    main()
