# -*- mode: python ; coding: utf-8 -*-
"""Builds the sidecar the Windows installer embeds (Sprint 83).

`scripts/build_sidecar.sh` is what actually invokes this — see it for the target-triple
naming Tauri's `externalBin` requires, and `installer/README.md` for what this produces
and why it has to run once per platform (PyInstaller does not cross-compile).

`collect_submodules` rather than a hand-picked hidden-import list for every `thursday_*`
package: PyInstaller's static import scan cannot see a module reached by name at runtime,
and while nothing in this codebase currently does that (checked, at the time this was
written), missing one here is not a build failure — it is a feature that silently 404s the
first time a user reaches it. The wider net costs build time, which is the CI job's problem,
not the installed app's.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent  # noqa: F821 — SPECPATH is injected by PyInstaller

#: Every source root pytest's `pythonpath` already lists (pyproject.toml), because this is
#: the same monorepo layout: `thursday_core` lives at `packages/core/thursday_core`, so
#: `packages/core` — not the repo root — is what has to be on the path for `import
#: thursday_core` to resolve. One list, read by both, would be nicer; pytest's config is
#: TOML and PyInstaller's is a Python file it executes, so there is no shared import site
#: without adding a third file just to hold six lines of strings.
SOURCE_ROOTS = [
    "packages/shared", "packages/core", "packages/agents", "packages/tools",
    "packages/memory", "packages/voice", "packages/vision", "packages/security",
    "packages/automation", "packages/devices", "packages/models",
    "services/api", "services/worker", "services/realtime",
]

THURSDAY_PACKAGES = [
    "thursday_shared", "thursday_core", "thursday_agents", "thursday_tools",
    "thursday_memory", "thursday_voice", "thursday_vision", "thursday_security",
    "thursday_automation", "thursday_devices", "thursday_models",
    "thursday_api", "thursday_worker", "thursday_realtime",
]

hidden_imports: list[str] = []
for package in [*THURSDAY_PACKAGES, "apps", "database", "alembic", "uvicorn"]:
    hidden_imports += collect_submodules(package)

# SQLAlchemy's dialect plugins are looked up by string ("sqlite+aiosqlite") and imported
# lazily at `create_engine()` time — invisible to PyInstaller's static scan of our own
# source, and not caught by `collect_submodules` on any package we actually import by
# name. Found the direct way: the frozen binary ran, reached `create_engine`, and raised
# `ModuleNotFoundError: No module named 'aiosqlite'` — the same "missed a name nobody
# thought of" failure mode Sprint 65's plain-language allowlist exists to avoid, here in a
# build tool instead of a UI string.
hidden_imports += ["aiosqlite"]

analysis = Analysis(  # noqa: F821 — PyInstaller injects its build-time globals
    [str(ROOT / "installer" / "sidecar_main.py")],
    pathex=[str(ROOT), *[str(ROOT / root) for root in SOURCE_ROOTS]],
    datas=[
        (str(ROOT / "alembic.ini"), "."),
        (str(ROOT / "database" / "migrations"), "database/migrations"),
    ],
    hiddenimports=hidden_imports,
)

pyz = PYZ(analysis.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="thursday-backend",
    # A service with no window of its own: stdout/stderr are what the Tauri sidecar reads
    # and logs (see apps/desktop/src-tauri/src/sidecar.rs), and hiding them would hide the
    # one place a startup failure — a stuck migration, a port already in use — can be seen.
    console=True,
)
