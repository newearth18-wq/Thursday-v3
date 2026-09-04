// Thursday's bundled backend, started by the shell that ships it (Sprint 83).
//
// EASY INSTALL's rule is that a normal user never sees a terminal, and the one this
// project had not yet closed was the biggest: every run of this app so far has assumed a
// backend was *already* listening on 127.0.0.1:8000, started by hand or by
// `scripts/dev.sh`. A packaged installer has no such hand. So a release build spawns its
// own — `installer/sidecar_main.py`, frozen by PyInstaller into the binary
// `bundle.externalBin` names in tauri.conf.json — and this module is the two decisions and
// the two actions around doing that.
//
// **A process that started is not a backend that works.** `spawn` returning `Ok` means the
// OS accepted the exec; it is not evidence Thursday can do anything, in the same sense a
// magic packet is not evidence a machine woke (ADR 0048) and a dispatched action is not
// evidence it took effect (ADR 0012, PART 5.1). So starting the sidecar and showing the
// window are two different moments: `wait_healthy` is the ACT -> VERIFY pair to `spawn`'s
// ACT, and only a real answer from the backend's own `/api/v1/health` moves the app on.

use std::sync::Mutex;
use std::time::Duration;

use tauri::{AppHandle, Manager};
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;

/// The name Tauri resolves against `bundle.externalBin` — see tauri.conf.json and
/// `scripts/build_sidecar.sh`, which is what actually produces the per-platform file this
/// name is short for.
pub const SIDECAR_NAME: &str = "thursday-backend";

/// How long a fresh install is given to migrate, seed and start listening before the
/// window is shown anyway. Generous on purpose — PyInstaller's onefile mode re-extracts
/// the whole bundle on every launch, and `installer/sidecar_main.py` runs a real schema
/// migration before it serves anything, both of which cost real seconds the sidecar has
/// never needed to before.
const HEALTH_TIMEOUT: Duration = Duration::from_secs(45);
const HEALTH_INTERVAL: Duration = Duration::from_millis(300);

/// Holds the running sidecar's handle, so it can be stopped on the way out.
///
/// A `Mutex<Option<_>>` rather than a plain field: `CommandChild::kill` consumes `self`, so
/// stopping it has to be able to *take* the value out from behind a shared `AppHandle`, and
/// `Option` is what lets "already stopped" and "never started" be the same, safe, no-op
/// case rather than two states callers have to tell apart.
#[derive(Default)]
pub struct SidecarState(pub Mutex<Option<CommandChild>>);

/// Whether *this* run should launch its own backend.
///
/// A developer running `tauri dev` already has one: `scripts/dev.sh` documents that as the
/// workflow, and it is a debug build (`cfg!(debug_assertions)`), so `is_dev_build` catches
/// it without needing a second signal. `api_url_override` reuses the same `THURSDAY_API_URL`
/// variable `api_base()` already reads for the emergency-stop commands — setting it says
/// "point at this one", and a packaged app that spawned its own backend anyway would be
/// answering a question nobody asked. A pure function of two inputs rather than a check
/// buried in `setup()`, so it is the thing under test rather than the whole startup path.
pub fn should_spawn(is_dev_build: bool, api_url_override: Option<&str>) -> bool {
    api_url_override.is_none() && !is_dev_build
}

/// Start the bundled backend and hand back its handle.
///
/// stdout/stderr are drained rather than dropped: a sidecar that never starts should say
/// why in the log — a stuck migration, a port already bound — not disappear the way the
/// emergency stop's silence did before Sprint 82 found it. Draining, not discarding, is
/// also what the API requires: an unread `Receiver` here would eventually block the child's
/// own writes.
pub fn spawn(app: &AppHandle) -> Result<CommandChild, tauri_plugin_shell::Error> {
    let (mut events, child) = app.shell().sidecar(SIDECAR_NAME)?.spawn()?;
    tauri::async_runtime::spawn(async move {
        use tauri_plugin_shell::process::CommandEvent;
        while let Some(event) = events.recv().await {
            match event {
                CommandEvent::Stderr(bytes) | CommandEvent::Stdout(bytes) => {
                    eprint!("[thursday-backend] {}", String::from_utf8_lossy(&bytes));
                }
                CommandEvent::Error(message) => {
                    eprintln!("[thursday-backend] {message}");
                }
                CommandEvent::Terminated(payload) => {
                    eprintln!("[thursday-backend] exited: {payload:?}");
                }
                _ => {}
            }
        }
    });
    Ok(child)
}

/// Poll the backend's own health endpoint until it answers or the timeout passes.
///
/// Never `sleep` once and trust the absence of a connection error — a socket accepting a
/// TCP handshake during startup is not the same claim as `/api/v1/health` answering `200`,
/// and the gap between those two moments is exactly where "started" stops meaning "works".
pub async fn wait_healthy(base: &str) -> bool {
    let client = reqwest::Client::new();
    let deadline = tokio::time::Instant::now() + HEALTH_TIMEOUT;
    while tokio::time::Instant::now() < deadline {
        if let Ok(response) = client.get(format!("{base}/api/v1/health")).send().await {
            if response.status().is_success() {
                return true;
            }
        }
        tokio::time::sleep(HEALTH_INTERVAL).await;
    }
    false
}

/// Stop the sidecar, if one is running. Idempotent — safe to call from more than one exit
/// path, which is deliberate: `main.rs` calls it both from the tray's "quit" and from the
/// app's own `RunEvent::Exit`, the same redundant-path posture the emergency stop already
/// uses for the same reason — the moment something is exiting is the worst moment to find
/// out one path was silently relying on the other.
pub fn stop(app: &AppHandle) {
    let Some(state) = app.try_state::<SidecarState>() else {
        return;
    };
    // `.take()` on its own line: the lock guard has to drop before `state` itself does, and
    // holding both in one `if let` ties their lifetimes together in a way the borrow
    // checker cannot untangle even though the guard is dropped well before this returns.
    let child = state.0.lock().unwrap().take();
    if let Some(child) = child {
        let _ = child.kill();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_release_build_with_no_override_spawns_its_own_backend() {
        assert!(should_spawn(false, None));
    }

    #[test]
    fn a_dev_build_never_spawns_a_second_backend() {
        // scripts/dev.sh already started one; a second on the same port either fails to
        // bind or shadows the one the developer is editing.
        assert!(!should_spawn(true, None));
        assert!(!should_spawn(true, Some("http://127.0.0.1:8000")));
    }

    #[test]
    fn an_explicit_override_is_a_request_to_use_that_backend_not_spawn_one() {
        assert!(!should_spawn(false, Some("http://192.168.1.20:8000")));
    }
}
