# 57. A phone is a screen, not a machine Thursday runs on

Date: Sprint 84 (a WebView shell for Android)

## Status

Accepted. Sibling to [0056](0056-a-packaged-thursday-starts-its-own-backend.md), which it
deliberately does not extend: Sprint 83's whole argument was that a packaged Thursday starts
its own backend, and this decision is where that argument stops.

## Context

The request was for Android reached by the smallest honest step: not a second frontend
rebuilt for mobile idioms, but the same shell Sprints 80–82 already built, wrapped in a
native window the way `apps/desktop` already wraps it for the other three platforms. Tauri 2
supports exactly that — the same Rust core, the same web frontend, compiled for
`aarch64-linux-android` and run inside a native Activity.

The question that could not be waved through is what a mobile build does about Sprint 83.
The tempting answer is "the same thing" — bundle a Python backend, spawn it, poll it healthy.
It is also wrong, and not only because Android sandboxing forbids an app spawning arbitrary
subprocesses. A phone is not the machine an owner's files, calendar, browser and other
devices are on. Thursday's whole design is one assistant that reaches *out* to where the
owner's things already are (§8's device protocol, the whole of V8's multi-device layer); a
backend running in a sandboxed process on the phone itself would be a second, disconnected
Thursday with none of that reach — arguably worse than not having a mobile app at all, since
it would answer questions with an authority it has no way to earn.

## Decision

**A phone is a screen onto a Thursday running somewhere else.** The Android build carries no
sidecar, no bundled Python, no `installer/sidecar_main.py` — it removes Sprint 83's whole
mechanism rather than reimplementing it, `#[cfg(desktop)]`-gated out of the shared
`lib.rs` module. What it needs instead is an address: where the Thursday it should talk to
already lives, on the owner's PC or home hub.

**The frontend asks, and keeps its answer editable.** `origin.ts` gained a stored override —
`localStorage`, real and persistent inside any WebView, Tauri's included — that `API_ORIGIN`
prefers over the built-in desktop default whenever one exists, on every platform alike. It
is not mobile-specific in its implementation: it is a generically useful "point this client
at a different Thursday" setting. `ServerConnect.tsx` is the screen that writes it, and it
does not ask only once — a stored address that is wrong or has gone quiet reaches the same
screen, pre-filled rather than blank, because a silent reconnect loop with no way to notice
a typo was ever the problem is worse than asking again.

**No platform detection.** The tempting way to decide *when* to ask is "is this Android" —
`@tauri-apps/plugin-os`, a user-agent sniff, something. `useRealtime` does none of that. It
counts consecutive connection failures, and asks after several in a row, whichever platform
is running: on a phone that has never been given an address, that is the very first attempt;
on desktop, Sprint 83's sidecar makes it vanishingly unlikely to ever fire at all, and if it
somehow does, asking for an address remains a reasonable thing to have happened, not a wrong
screen for the situation. A signal that happens to be true on mobile is worth more than a
boolean that says so, for the same reason `should_spawn` (ADR 0056) is a function of a build
mode and an env var rather than of "what OS is this": the thing worth asking is what state
to be in, not what device is asking.

**The window shows immediately.** Desktop hides the main window until its own sidecar proves
healthy, because showing it earlier would mean lying about a backend that was not there yet.
Mobile has no backend of its own to wait for — `run()`'s mobile branch calls `show_window`
straight away — and the connect screen, not a hidden window, is what an owner meets first
until they have told Thursday where to look.

## Consequences

`main.rs` is now three lines; everything that used to be there moved to `lib.rs` behind
`#[cfg_attr(mobile, tauri::mobile_entry_point)]`, which is what a mobile launcher calls into
directly rather than through a `fn main`. `Cargo.toml` gained a `[lib]` target
(`staticlib`/`cdylib`/`rlib`) for the same reason. Every desktop behaviour from Sprints 80–83
— the HUD, the avatar, the sidecar, the tray — is unchanged: the restructuring was verified
by rebuilding the desktop binary and reconfirming the sidecar still lands exactly where
`Shell::sidecar()` looks for it.

**The avatar does not exist on Android**, and this is a real capability gap rather than an
oversight: a second, transparent, always-on-top, click-through OS window is a desktop
windowing concept with no equivalent in Android's one-Activity-per-app model. What ships on
mobile is the HUD only.

**This was source-level readiness, and CI's `android` job found the gap between that and a
real build on its very first run** — the same way ADR 0056's `shell` job failed on its own
first run, and for a related reason. `tauri.conf.json`'s `externalBin` is not itself
platform-conditional: it is read by `tauri-build`'s build script for *whatever* target
`cargo build` is currently compiling, desktop or mobile alike, because the JSON file makes
no distinction. Sprint 83 declared it once, in the base config, correctly for every desktop
target — and, it turned out, for Android too, where it demanded a sidecar binary that must
never exist (this ADR's whole argument is that one should not). The fix is Tauri's own
mechanism for exactly this: `externalBin` moved out of the base config into
`tauri.linux.conf.json`, `tauri.windows.conf.json` and `tauri.macos.conf.json` — platform
overlays merged in only for the platform named, so it is present on every desktop build and
absent on Android without either side needing to ask what platform it is running on, which
is the same rule the rest of this ADR argues for one layer up. Verified by removing the
built sidecar and confirming a plain desktop `cargo check` still refuses to proceed without
it — the overlay is genuinely enforced, not merely absent by coincidence.

No Android SDK, NDK, or emulator exists in the environment this was built in, so no `.apk`
has been installed on a device or emulator, and this fix's Android half is confirmed only
by CI, not locally. A person with a phone is what closes that gap for good, the same way
ADR 0056 named a real Windows machine as the thing that closes its own.
