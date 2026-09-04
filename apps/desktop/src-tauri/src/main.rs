// Thursday's desktop shell.
//
// Deliberately thin: everything Thursday does lives behind the API, and the window is a
// client of it like any other. Two things belong here and nowhere else —
//
//  * the windows themselves, because a browser tab is not a personal assistant; and
//  * the tray's emergency stop, which posts to the API directly from Rust rather than
//    asking the webview to do it. If the interface has locked up, that is precisely when
//    the owner most wants to stop what is running (PART 98).
//
// Sprint 82 added the second window. The avatar has to be *above* whatever the owner is
// actually working in, and no element inside another window can be — so it is a real
// window: transparent, undecorated, always on top, and ignoring the mouse entirely, which
// is the difference between a companion and something in the way.
//
// Sprint 83 added the third thing this file owns: starting Thursday's own backend. Every
// run of this app before it assumed 127.0.0.1:8000 was already answering, started by hand
// or by `scripts/dev.sh` — a fine assumption for a developer and a false one for whoever
// double-clicks an installer. `sidecar.rs` is the mechanism; the two decisions of *whether*
// to spawn one and *when* to stop hiding the window live here, next to everything else this
// app decides about its own windows.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod sidecar;

use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    AppHandle, Emitter, Manager, RunEvent, WebviewUrl, WebviewWindowBuilder, WindowEvent,
};

/// The main window's label, and the avatar's. Used rather than typed at each call site so
/// a rename cannot leave one branch pointing at a window that no longer exists.
const MAIN: &str = "main";
const AVATAR: &str = "avatar";

/// Where the local Thursday API is listening. Overridable so a second install, or a
/// developer running the API on another port, does not need a rebuild.
fn api_base() -> String {
    std::env::var("THURSDAY_API_URL").unwrap_or_else(|_| "http://127.0.0.1:8000".into())
}

/// Stop every running task and drop standing permissions.
///
/// Exposed to the frontend *and* wired to the tray, so there are two independent paths to
/// it. It returns the API's own reply rather than a bare bool: the owner is told what was
/// actually stopped, not merely that something was attempted.
#[tauri::command]
async fn emergency_stop() -> Result<serde_json::Value, String> {
    post(
        &format!("{}/api/v1/emergency/stop", api_base()),
        serde_json::json!({"scope": "all"}),
    )
    .await
}

#[tauri::command]
async fn release_lockdown() -> Result<serde_json::Value, String> {
    post(
        &format!("{}/api/v1/emergency/release", api_base()),
        serde_json::json!({}),
    )
    .await
}

async fn post(url: &str, body: serde_json::Value) -> Result<serde_json::Value, String> {
    reqwest::Client::new()
        .post(url)
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("could not reach Thursday: {e}"))?
        .json::<serde_json::Value>()
        .await
        .map_err(|e| format!("unreadable reply from Thursday: {e}"))
}

fn show_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window(MAIN) {
        let _ = window.show();
        let _ = window.set_focus();
    }
    // Bringing Thursday to the front is the one unambiguous "I am here": the avatar's whole
    // job is to be present while the owner is elsewhere, so it stands down.
    set_avatar(app, false);
}

/// Show or hide the avatar. Silent when there is no avatar window — on a system where the
/// transparent window could not be created, everything else must still work.
fn set_avatar(app: &AppHandle, visible: bool) {
    if let Some(window) = app.get_webview_window(AVATAR) {
        let _ = if visible {
            window.show()
        } else {
            window.hide()
        };
    }
}

/// Build the avatar window: a full-screen, transparent, click-through overlay.
///
/// `set_ignore_cursor_events` is not a detail. Without it this window is a sheet of glass
/// over the whole desktop that swallows every click, which would make Thursday the most
/// hated thing on the machine within about ten seconds.
fn build_avatar(app: &AppHandle) -> tauri::Result<()> {
    let window = WebviewWindowBuilder::new(app, AVATAR, WebviewUrl::App("index.html".into()))
        .title("Thursday")
        .transparent(true)
        .decorations(false)
        .always_on_top(true)
        .skip_taskbar(true)
        .resizable(false)
        .visible(false)
        // Which half of the bundle to render, decided before the bundle runs. A flag rather
        // than a hash or a query string, because those have to survive URL handling on
        // three platforms to arrive intact, and a window that quietly loads the wrong half
        // is a bug nobody notices until the packaged build.
        .initialization_script("window.__THURSDAY_AVATAR__ = true;")
        .build()?;

    window.set_ignore_cursor_events(true)?;
    if let Some(monitor) = window.primary_monitor()? {
        window.set_position(*monitor.position())?;
        window.set_size(*monitor.size())?;
    }
    Ok(())
}

/// Bring the backend up, then bring the window up — never the other way round.
///
/// `installer/sidecar_main.py` migrates and seeds before it serves, which on a fresh
/// install is real work, not a formality; showing the window first would put the owner in
/// front of a Thursday that answers every turn with "reconnecting…" for however long that
/// takes, which is indistinguishable from broken. A failed or slow start still shows the
/// window rather than hanging indefinitely — `useRealtime`'s reconnect-with-backoff already
/// renders that state honestly (Sprint 80), which is a better failure mode than an app that
/// never opens.
async fn start_backend_then_show(app: AppHandle) {
    match sidecar::spawn(&app) {
        Ok(child) => {
            if let Some(state) = app.try_state::<sidecar::SidecarState>() {
                *state.0.lock().unwrap() = Some(child);
            }
        }
        Err(error) => eprintln!("Thursday's backend could not be started: {error}"),
    }
    if !sidecar::wait_healthy(&api_base()).await {
        eprintln!("Thursday's backend did not answer within the startup window");
    }
    show_window(&app);
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(sidecar::SidecarState::default())
        .invoke_handler(tauri::generate_handler![emergency_stop, release_lockdown])
        .setup(|app| {
            // The avatar is best-effort: a machine whose compositor cannot give us a
            // transparent always-on-top window still gets the whole of Thursday, minus a
            // robot. Failing to start over a decoration would be the wrong trade.
            if let Err(error) = build_avatar(app.handle()) {
                eprintln!("the avatar window could not be created: {error}");
            }

            // The main window ships `"visible": false` (tauri.conf.json) precisely so this
            // decision — show it now, or hold it for a backend that has not proven itself
            // yet — is made once, here, rather than the window flashing on and then having
            // nothing to say.
            let override_url = std::env::var("THURSDAY_API_URL").ok();
            if sidecar::should_spawn(cfg!(debug_assertions), override_url.as_deref()) {
                tauri::async_runtime::spawn(start_backend_then_show(app.handle().clone()));
            } else {
                // The documented dev workflow (`scripts/dev.sh`) or an explicit
                // `THURSDAY_API_URL` already has a backend — show immediately, exactly the
                // behaviour this app had before Sprint 83.
                show_window(app.handle());
            }

            let show = MenuItem::with_id(app, "show", "Open Thursday", true, None::<&str>)?;
            let hide = MenuItem::with_id(app, "hide", "Hide the avatar", true, None::<&str>)?;
            let stop = MenuItem::with_id(app, "stop", "Stop everything", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &hide, &stop, &quit])?;

            TrayIconBuilder::new()
                .icon(app.default_window_icon().unwrap().clone())
                .menu(&menu)
                .tooltip("Thursday")
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => show_window(app),
                    // One direction only. Turning the avatar back on is done by leaving
                    // Thursday's window, which is the thing it exists to respond to — a
                    // toggle that could be left "on" while the owner is looking straight
                    // at the HUD would put two of Thursday on one screen.
                    "hide" => set_avatar(app, false),
                    "stop" => {
                        // Fire and report: the tray cannot block on a network call, but a
                        // silent failure here would be the worst possible one, so the
                        // window is brought up to show whatever came back.
                        let handle = app.clone();
                        tauri::async_runtime::spawn(async move {
                            let outcome = emergency_stop().await;
                            show_window(&handle);
                            let _ = handle.emit("emergency.stopped", outcome.ok());
                        });
                    }
                    "quit" => {
                        // Called directly here as well as from `RunEvent::Exit` below —
                        // the same redundant-path posture the emergency stop already uses.
                        // Quitting is the one moment nothing should be relying on the other
                        // path having fired first.
                        sidecar::stop(app);
                        app.exit(0);
                    }
                    _ => {}
                })
                .build(app)?;

            Ok(())
        })
        .on_window_event(|window, event| {
            if window.label() != MAIN {
                return;
            }
            match event {
                // Closing the window hides it rather than killing the process: a task
                // Thursday is running does not stop because the owner cleared their screen.
                // Quitting is an explicit choice, from the tray.
                WindowEvent::CloseRequested { api, .. } => {
                    api.prevent_close();
                    let _ = window.hide();
                    set_avatar(window.app_handle(), true);
                }
                // "Somewhere else", defined: the owner is working in another application.
                // That is the whole trigger — not a timer, not idle detection, and nothing
                // that watches what they are doing. Thursday knows it is not the window in
                // front, and that is all it knows.
                WindowEvent::Focused(focused) => set_avatar(window.app_handle(), !focused),
                _ => {}
            }
        })
        .build(tauri::generate_context!())
        .expect("Thursday failed to start")
        .run(|app, event| {
            // The general path: OS shutdown, Cmd+Q, anything that is not the tray's own
            // "quit" item. A leaked child process here is not merely untidy — it is a
            // second Thursday backend still bound to the port the next launch needs.
            if let RunEvent::Exit = event {
                sidecar::stop(app);
            }
        });
}
