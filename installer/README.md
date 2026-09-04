# The sidecar (Sprint 83)

What makes "one .exe, no terminal" true. EASY INSTALL's rule is that a normal user never
sees Python, PostgreSQL, Redis, Docker, Node, or a command line — and every sprint before
this one satisfied that for what happens *after* Thursday's backend is already running.
This is the piece that starts it.

## What is here

- **`sidecar_main.py`** — the entrypoint. Migrates the schema, seeds the catalogue, then
  serves, in that order and never out of it (see its own docstring, and ADR 0056). This is
  what gets frozen — not `apps.server`, which has always assumed those three steps already
  happened by hand.
- **`thursday-backend.spec`** — the PyInstaller spec that freezes it, plus `alembic.ini` and
  `database/migrations` bundled as data so the frozen binary can migrate itself.

## How it gets built

```bash
uv pip install -e ".[installer]"   # or: pip install -e ".[installer]"
bash scripts/build_sidecar.sh
```

produces `apps/desktop/src-tauri/binaries/thursday-backend-<target-triple>[.exe]` — the
name `tauri-plugin-shell`'s bundler (`external_binaries()`, invoked from `tauri-build`'s
`build.rs`) expects for the platform `rustc` is currently targeting. **This has to run once
per platform a bundle ships for.** PyInstaller does not cross-compile; a Linux host cannot
produce the Windows binary, and vice versa. `.github/workflows/ci.yml`'s `shell` job runs
it on Linux (so `cargo check`/`clippy` — which now require the sidecar to exist, once
`bundle.externalBin` is set — keep working on every PR); `windows-installer` runs it on a
real `windows-latest` runner and goes on to produce the actual NSIS `.exe`.

Once built, `apps/desktop/src-tauri/src/sidecar.rs` is what spawns it — see that file and
ADR 0056 for the ACT → VERIFY startup sequence (spawn, then poll `/api/v1/health`, then
show the window) and why the window ships hidden until that verification passes.

## Verifying it without a Windows machine

Cross-platform packaging can only be proven this far without one: build for the *host*
platform and run the frozen binary standalone.

```bash
bash scripts/build_sidecar.sh
env THURSDAY_DATA_DIR=/tmp/thursday-verify \
    apps/desktop/src-tauri/binaries/thursday-backend-$(rustc -vV | sed -n 's/^host: //p') &
curl http://127.0.0.1:8000/api/v1/health
```

A healthy response means migrate → seed → serve worked end to end, on this host's OS. It
does not mean the Windows build will — only a Windows build does that, which is what the
`windows-installer` CI job is for.

## What is not proven here

That the full desktop shell — window, tray, sidecar spawn, health-poll, all of it —
actually opens on a real desktop. `cargo build --release` was shown to place the sidecar
exactly where `Shell::sidecar()`'s own path resolution looks for it (confirming the naming
convention above is the one Tauri's bundler actually expects), and the frozen binary itself
was run and verified standalone. The GUI event loop itself has never opened a window in the
container this was built in — a pre-existing, unrelated fault in headless X — so the one
thing genuinely unverified is Tauri's window and tray rendering on a real machine. See ADR
0055 and ADR 0056 for what that leaves unproven, and CI's `windows-installer` job for the
first automated step toward closing it.
