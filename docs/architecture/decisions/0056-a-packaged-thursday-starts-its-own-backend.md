# 56. A packaged Thursday starts its own backend

Date: Sprint 83 (EASY INSTALL — the Windows installer)

## Status

Accepted. Extends [0049](0049-the-shipped-configuration-is-the-product.md)'s argument — the
configuration that ships is the one that runs — to the process that has to be running before
any of it matters.

## Context

EASY INSTALL's rule is that a normal user never sees a terminal. Every sprint before this one
satisfied that rule for what happens *after* Thursday is running: the setup wizard, the plain
language, the repair button. None of them touched the assumption underneath all of it, because
every one of them was developed and tested against a backend a developer had already started —
by hand, or by `scripts/dev.sh`.

A packaged installer has no hand. `apps/desktop`'s window has always been "a client of the API
like any other" (main.rs's own opening line), which is the right design for the window and
was silently standing in for a real answer to "then what starts the API". Double-clicking an
installed Thursday would have opened a window that spent its whole session saying
"reconnecting…" — the most literal instance yet of this project's recurring bug class: the
part that works and the part that ships are not the same claim, and nothing had checked.

## Decision

**The packaged app is the only thing that spawns a backend.** `sidecar::should_spawn` is a
pure function of two inputs — a dev build, and `THURSDAY_API_URL` being set — and it is `false`
whenever either is true. `scripts/dev.sh` remains the documented way to run one by hand; a
developer's `tauri dev` window behaves exactly as it did before this sprint, unchanged, because
changing that behaviour would be answering a question a developer did not ask.

**A process that started is not a backend that works.** `spawn()` returning `Ok` means the OS
accepted the exec — the same distance between "sent" and "verified" ADR 0048 already drew for
a magic packet, and between "dispatched" and "observed" ADR 0012 drew for an action. So
`spawn` and `wait_healthy` are two different calls, and the window stays hidden — `tauri.conf.json`
ships the main window `"visible": false` for exactly this — until the real `/api/v1/health`
endpoint answers or forty-five seconds pass. A failed or slow start still shows the window
rather than hanging forever: the reconnect-with-backoff Sprint 80 already built renders that
honestly, which is a better failure mode than an app that never opens.

**The backend that gets bundled makes its own assumptions true, in order.**
`installer/sidecar_main.py` — frozen by PyInstaller into the file `bundle.externalBin` names —
runs `ensure_dirs()`, then `alembic upgrade head`, then the idempotent seed, then serves.
`python -m apps.server` has always assumed those three things were already true; a developer
who ran them by hand would notice immediately if one failed, because the next one is a
separate command they would not go on to type. Frozen into one process for someone who never
typed any of them, the ordering has to be code. A test asserts the order directly, and that a
failure at any step makes the ones after it unreachable — not merely unlikely.

**Nowhere near the installed program's own directory.** `settings.data_dir` composes the
SQLite path, and on Windows the installed location is Program Files, which a standard user
cannot write to. The Rust side points `THURSDAY_DATA_DIR` at the OS's actual per-user
application-data directory before it ever spawns the process; the Python side never has to
know it is frozen, only that the directory it was told about is real.

**Stopping the sidecar has two independent paths**, the same posture ADR 0055 already used for
the avatar: the tray's "quit" item calls `sidecar::stop` directly, and the app's own
`RunEvent::Exit` calls it again. `stop` is idempotent — the second call finds nothing to kill —
which is what makes calling it from two places safe rather than a race. A leaked backend
process is not merely untidy: it is a second Thursday still bound to the port the next launch
needs.

## Consequences

PyInstaller does not cross-compile, so the sidecar has to be built once per platform whose
installer needs one — `scripts/build_sidecar.sh` is that step, and CI now runs it before
`cargo clippy` for exactly the reason stated in its own comment: `tauri-build`'s build script
calls `copy_binaries` on every `cargo build` once `externalBin` is set, so a missing sidecar
fails compilation, not just packaging. That is deliberate — a broken sidecar build is caught
by `cargo check`, not discovered the first time somebody runs the installer, and it is why the
`shell` CI job (added in Sprint 82, and itself broken on its first real run) now builds the
frontend, the sidecar, *and* the Rust, in that order, before it claims anything passed.

Startup on a fresh install costs real seconds it never used to: PyInstaller's onefile mode
re-extracts the whole bundle on every launch, and a first run pays for a real schema
migration on top of that. Both are within the forty-five-second window, verified against the
actual frozen binary — run standalone, from cold, with nothing pre-seeded — which answered
`/api/v1/health` in four seconds. A developer's `tauri dev` window is unaffected either way.

**This remains unverified under the one thing that matters most: a real Windows installer,
built and run on Windows.** The mechanism was proven as far as this environment allows —
the frozen Python binary was built, run standalone, and shown to migrate, seed, serve and
answer `/api/v1/health` and `/api/v1/expression` correctly, twice, the second time idempotently;
`cargo build --release` was shown to copy that binary into the exact `target/release/` location
`Shell::sidecar()`'s own `relative_command_path` resolves at runtime, confirming the naming
convention `scripts/build_sidecar.sh` uses is the one Tauri's bundler expects. What was not
reachable here: the full Tauri window and event loop crashed under this container's headless
X server on an unrelated, pre-existing fault — a `glib::main_context_channel::dispatch` panic
inside `tao`'s GTK backend, present with or without a real D-Bus session, and unrelated to
anything this sprint changed (Sprint 82's avatar window carries the same "never run under
real Tauri" caveat, for the same underlying reason). CI's new `windows-installer` job runs
`cargo tauri build --bundles nsis` on a real `windows-latest` runner and is the first thing
that will produce and can be asked to smoke-test an actual `.exe`; a human running the
installed application on a real Windows desktop is what closes this gap for good.
