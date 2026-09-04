"""The sidecar's own startup order (Sprint 83).

`installer/sidecar_main.py` is what a packaged install actually runs, and it makes three
assumptions true that a developer normally makes true by hand, in three separate terminal
commands: the data directory exists, the schema is current, the seed data is in place. Here
they are one process, so getting the order wrong is not a habit slip a developer would
catch — it is a crash the first person to double-click the installer meets.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from installer import sidecar_main


def test_migrate_then_seed_then_serve_in_that_order():
    order: list[str] = []

    with (
        patch.object(sidecar_main, "migrate", side_effect=lambda: order.append("migrate")),
        patch.object(sidecar_main, "seed", side_effect=lambda: order.append("seed")),
        patch.object(sidecar_main, "serve", side_effect=lambda: order.append("serve")),
        patch("thursday_core.config.get_settings") as get_settings,
    ):
        get_settings.return_value.ensure_dirs = lambda: order.append("ensure_dirs")
        sidecar_main.main()

    assert order == ["ensure_dirs", "migrate", "seed", "serve"]


def test_a_failed_migration_never_reaches_seed_or_serve():
    """A schema that did not come up must not be seeded or served against.

    Seeding against a schema migrations have not reached is the crash a person meets on
    first launch; serving against one is worse. Both must be unreachable, not merely
    unlikely, when the step before them raises.
    """
    called: list[str] = []

    with (
        patch.object(sidecar_main, "migrate", side_effect=RuntimeError("schema is stuck")),
        patch.object(sidecar_main, "seed", side_effect=lambda: called.append("seed")),
        patch.object(sidecar_main, "serve", side_effect=lambda: called.append("serve")),
        patch("thursday_core.config.get_settings") as get_settings,
    ):
        get_settings.return_value.ensure_dirs = lambda: None
        with pytest.raises(RuntimeError, match="schema is stuck"):
            sidecar_main.main()

    assert called == [], f"seed/serve ran after migrate failed: {called}"


def test_a_failed_seed_never_reaches_serve():
    called: list[str] = []

    with (
        patch.object(sidecar_main, "migrate", side_effect=lambda: None),
        patch.object(sidecar_main, "seed", side_effect=RuntimeError("catalogue is stuck")),
        patch.object(sidecar_main, "serve", side_effect=lambda: called.append("serve")),
        patch("thursday_core.config.get_settings") as get_settings,
    ):
        get_settings.return_value.ensure_dirs = lambda: None
        with pytest.raises(RuntimeError, match="catalogue is stuck"):
            sidecar_main.main()

    assert called == [], f"serve ran after seed failed: {called}"


def test_bundled_root_falls_back_to_the_project_root_when_not_frozen():
    """Running from source — which is how this test and a developer both exercise it —
    there is no `sys._MEIPASS`, and the real alembic.ini already lives at the project root."""
    root = sidecar_main.bundled_root()
    assert (root / "alembic.ini").is_file()
    assert (root / "database" / "migrations").is_dir()


def test_bundled_root_prefers_meipass_when_frozen():
    with patch.object(sidecar_main.sys, "_MEIPASS", "/frozen/extract/dir", create=True):
        assert sidecar_main.bundled_root() == sidecar_main.Path("/frozen/extract/dir")


# --------------------------------------------------- where a packaged Thursday keeps its data


SIDECAR_RS = Path(__file__).resolve().parents[2] / "apps/desktop/src-tauri/src/sidecar.rs"


def data_dir_env_name() -> str:
    """The variable name, derived from the model rather than typed out again.

    Renaming `Settings.data_dir` or the `env_prefix` therefore breaks the test below, which
    is the point: the Rust side hard-codes this string, and the only thing that can keep the
    two honest is deriving one of them.
    """
    from thursday_core.config import Settings

    prefix = Settings.model_config["env_prefix"]
    return f"{prefix}data_dir".upper()


def test_the_data_directory_is_configurable_by_environment():
    """The contract the Rust side depends on, checked on the Python side."""
    from thursday_core.config import Settings

    with patch.dict("os.environ", {data_dir_env_name(): "/tmp/thursday-test-data"}):
        settings = Settings()

    assert settings.data_dir == Path("/tmp/thursday-test-data")
    assert settings.data_dir.is_absolute()


def test_the_shell_points_the_backend_at_a_writable_directory():
    """Sprint 87, and the bug it exists for.

    `settings.yaml` leaves `data_dir` at its code default of `var` — a *relative* path,
    which resolves against the sidecar's working directory. In development that is the
    repository and it is exactly right. Installed by NSIS it is `C:\\Program Files\\Thursday`,
    which a normal user cannot write to, so `ensure_dirs()` raised `PermissionError`, the
    backend never served, and the window appeared after the 45-second timeout to say
    "reconnecting…" forever — the precise failure ADR 0056 exists to prevent, reintroduced
    one layer below it.

    `sidecar_main.main()` already described the fix as though it were in place — "wherever
    the Rust side pointed THURSDAY_DATA_DIR" — and that comment was the **only** occurrence
    of the name in the whole repository. Nothing set it.

    This reads the Rust source because there is no other way to check a cross-language
    contract from here, and it looks for the variable's real name rather than for prose
    about it: the name is derived from the pydantic model above, so the two sides cannot
    drift apart without something failing.
    """
    source = SIDECAR_RS.read_text(encoding="utf-8")
    name = data_dir_env_name()

    assert f".env({name}" in source or f'"{name}"' in source, (
        f"the shell never sets {name}, so an installed backend resolves its data "
        f"directory against Program Files"
    )
    # And it is passed to the child rather than merely named in a comment.
    assert ".env(DATA_DIR_ENV" in source, "the constant is declared but never handed to the sidecar"
    assert "app_data_dir()" in source, "the directory must come from the OS, not be invented"
