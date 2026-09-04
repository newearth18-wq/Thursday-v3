"""Thursday's bundled backend — what the Windows installer's sidecar actually runs
(Sprint 83, EASY INSTALL §"the deployment editions").

`python -m apps.server` assumes migrations have already run and a database already exists —
a fine assumption for a developer who typed ``alembic upgrade head`` a moment ago, and a
false one for whoever double-clicked an installer. This is the entrypoint PyInstaller
freezes into ``thursday-backend``: it makes those assumptions true itself, in order, and
only then serves.

**Migrate, then seed, then serve — never out of that order.** Seeding against a schema
migrations have not yet reached is the crash a person meets on first launch, and it is
exactly the ordering bug a dev workflow hides: a human who runs the three steps by hand
would notice immediately if migrate failed, because seed and serve are separate commands
they would not go on to type. Here they are one process, so the ordering has to be code
rather than a habit. ``tests/unit/test_sidecar_entrypoint.py`` asserts the order directly,
and that a failure in one step stops the ones after it.

Nothing here talks to a model, a cloud provider, or the network beyond binding a local
port — the same offline-by-default posture ADR 0049 already established for the shipped
config, applied to the thing that reads it.
"""

from __future__ import annotations

import sys
from pathlib import Path


def bundled_root() -> Path:
    """Where this run's data files live.

    PyInstaller's onefile mode extracts everything it collected into a temporary directory
    and sets ``sys._MEIPASS`` to it. Running this file directly from source — which is how
    the entrypoint test exercises it, and how a developer would debug it — there is no such
    attribute, and the project root (three parents up from this file) is where the real
    ``alembic.ini`` and ``database/migrations`` already live.
    """
    frozen_root = getattr(sys, "_MEIPASS", None)
    return Path(frozen_root) if frozen_root else Path(__file__).resolve().parents[1]


#: Written into the data directory on first launch, with **everything commented out**.
#:
#: That is the whole design. An active key here would become the value that runs, and would
#: then go on running after the code default beside it changed — ADR 0049's argument in
#: reverse. Commented, the file is documentation a person can act on: the defaults still
#: govern until somebody deliberately uncomments a line.
STARTER_SETTINGS = """\
# Thursday's settings.
#
# Everything here is commented out, so Thursday is running on its built-in defaults.
# Uncomment a line to change one. Restart Thursday afterwards (tray icon -> Quit, then
# open it again) — this file is read once, at startup.
#
# Secrets are NOT settings and never belong in this file. Where a key is needed, this
# file names its *handle*, and the value comes from the vault. On this machine that is
# the process environment: a handle of `anthropic_api_key` is read from the variable
# THURSDAY_SECRET_ANTHROPIC_API_KEY.

# identity:
#   owner_name: Owner
#   locale: th-TH
#   timezone: Asia/Bangkok
#   # 0 off - 1 low - 2 normal - 3 high. How often Thursday speaks up unprompted.
#   proactivity: 2
#   # 0 suggest-only - 1 safe actions - 2 moderate - 3 high. How much it does unasked.
#   # Raising this never relaxes the ASK_ALWAYS set (ADR 0009).
#   autonomy: 2

# models:
#   # rule    - offline and deterministic. The default, and what ships working.
#   # ollama  - a model running on this machine. Install Ollama first.
#   # anthropic - the cloud. Needs THURSDAY_SECRET_ANTHROPIC_API_KEY in the environment.
#   backend: rule
#   allow_cloud: true
#   ollama_url: http://127.0.0.1:11434
#   ollama_model: llama3.1:8b
#   anthropic_key_handle: anthropic_api_key

# teaching: normal      # off | on_request | low | normal | high
"""


def ensure_settings(path: Path) -> bool:
    """Put a settings file where an installed Thursday can find it, if there is not one.

    Sprint 90. Until this existed, a packaged Thursday read **no settings file at all**:
    `settings.yaml` is not bundled by the installer, and the path it looks for is relative,
    so it resolved against wherever Windows started the executable. Every value came from
    the code defaults and the only way to change one was a Windows environment variable —
    which is not a way to configure a product whose stated premise is that a normal user
    never opens a terminal.

    Never overwrites. A person who has edited this file has said something, and a launcher
    that quietly reverts it is worse than one that never wrote it.
    """
    if path.exists():
        return False
    path.write_text(STARTER_SETTINGS, encoding="utf-8")
    return True


def migrate() -> None:
    """Bring the schema to head. A no-op on a database already there."""
    from alembic import command
    from alembic.config import Config

    root = bundled_root()
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "database" / "migrations"))
    command.upgrade(config, "head")


def seed() -> None:
    """The catalogue a fresh install needs to start: agents, tools, permission defaults.

    Idempotent by the seed module's own contract — CI runs it twice to prove that — so
    running it on every launch rather than only the first is one fewer state to get wrong,
    not a second thing to get right.
    """
    from database.seeds import main as seed_main

    seed_main()


def serve() -> None:
    """Bind and block. This is the sidecar's foreground process for as long as it runs."""
    import uvicorn
    from thursday_api.app import create_app
    from thursday_core.config import get_settings

    settings = get_settings()
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


def main() -> None:
    from thursday_core.config import get_settings

    settings = get_settings()
    # Sprint 83's whole reason to exist: the directory `data_dir` names is wherever the
    # Rust side pointed THURSDAY_DATA_DIR (the OS's per-user app-data folder), not wherever
    # this executable happens to be installed — which on Windows is Program Files, and not
    # writable by a normal user (see sidecar.rs).
    settings.ensure_dirs()
    # After `ensure_dirs`, which is what makes the directory exist, and before anything
    # reads a setting — though nothing this launch does will pick up an edit made now, which
    # is why the file itself says to restart.
    ensure_settings(settings.data_dir / "settings.yaml")
    migrate()
    seed()
    serve()


if __name__ == "__main__":
    main()
