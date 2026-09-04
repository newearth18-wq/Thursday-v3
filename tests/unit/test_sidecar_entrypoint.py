"""The sidecar's own startup order (Sprint 83).

`installer/sidecar_main.py` is what a packaged install actually runs, and it makes three
assumptions true that a developer normally makes true by hand, in three separate terminal
commands: the data directory exists, the schema is current, the seed data is in place. Here
they are one process, so getting the order wrong is not a habit slip a developer would
catch — it is a crash the first person to double-click the installer meets.
"""

from __future__ import annotations

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
