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

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    AppHandle, Emitter, Manager, WebviewUrl, WebviewWindowBuilder, WindowEvent,
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
    post(&format!("{}/api/v1/emergency/stop", api_base()), serde_json::json!({"scope": "all"}))
        .await
}

#[tauri::command]
async fn release_lockdown() -> Result<serde_json::Value, String> {
    post(&format!("{}/api/v1/emergency/release", api_base()), serde_json::json!({})).await
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
        let _ = if visible { window.show() } else { window.hide() };
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

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![emergency_stop, release_lockdown])
        .setup(|app| {
            // The avatar is best-effort: a machine whose compositor cannot give us a
            // transparent always-on-top window still gets the whole of Thursday, minus a
            // robot. Failing to start over a decoration would be the wrong trade.
            if let Err(error) = build_avatar(app.handle()) {
                eprintln!("the avatar window could not be created: {error}");
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
                    "quit" => app.exit(0),
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
        .run(tauri::generate_context!())
        .expect("Thursday failed to start");
}
