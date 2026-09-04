"""Claims about the desktop shell that only Rust source can answer.

Sprint 88. There is no way to unit-test "opening the app twice brings the first one to the
front" from here — it needs a windowing system and two processes. What *is* checkable is the
one structural condition the guard depends on, and it is the condition somebody tidying the
builder chain would break without noticing.
"""

from __future__ import annotations

from pathlib import Path

LIB_RS = Path(__file__).resolve().parents[2] / "apps/desktop/src-tauri/src/lib.rs"
CARGO = Path(__file__).resolve().parents[2] / "apps/desktop/src-tauri/Cargo.toml"


def test_the_shell_refuses_to_start_twice():
    """The bug the first real install produced.

    Tauri starts as many copies of an app as you ask it to. Double-clicking the shortcut a
    second time therefore started a whole second Thursday: a second tray icon, a second
    always-on-top avatar window standing somewhere else on the same desktop, and a second
    sidecar racing the first for port 8000. What the owner saw was two robots, with no way
    to tell which was which — which is how this was found, in a screenshot.
    """
    source = LIB_RS.read_text(encoding="utf-8")
    assert "tauri_plugin_single_instance::init" in source, (
        "nothing stops a second copy of Thursday from starting"
    )
    assert "tauri-plugin-single-instance" in CARGO.read_text(encoding="utf-8")


def test_the_single_instance_guard_is_registered_before_every_other_plugin():
    """Its own documentation's one requirement, and the reason this test exists.

    The plugin works by claiming a lock before anything else initialises, so registering it
    after another plugin quietly does nothing — no error, no warning, just two Thursdays
    again the next time somebody double-clicks. A reordering of the builder chain is exactly
    the sort of tidy-up that looks harmless in review.
    """
    source = LIB_RS.read_text(encoding="utf-8")
    plugins = [
        (source.index(call), call)
        for call in ("tauri_plugin_single_instance::init", "tauri_plugin_shell::init")
        if call in source
    ]
    first = min(plugins)[1]
    assert first == "tauri_plugin_single_instance::init", (
        f"{first} is registered before the single-instance guard, which silently disables it"
    )


def test_opening_thursday_again_brings_the_window_forward():
    """The guard must not merely refuse the second copy — it has to do the thing the person
    was asking for. Somebody double-clicking the icon wants Thursday in front of them, and
    an app that appears to do nothing at all reads as broken rather than as already open."""
    source = LIB_RS.read_text(encoding="utf-8")
    start = source.index("tauri_plugin_single_instance::init")
    body = source[start : start + 300]
    assert "show_window" in body, "the second launch is swallowed without showing anything"
