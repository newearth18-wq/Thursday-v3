// Thursday's desktop shell.
//
// Deliberately thin: everything Thursday does lives behind the API, and the window is a
// client of it like any other. Two things belong here and nowhere else —
//
//  * the window itself, because a browser tab is not a personal assistant; and
//  * the tray's emergency stop, which posts to the API directly from Rust rather than
//    asking the webview to do it. If the interface has locked up, that is precisely when
//    the owner most wants to stop what is running (PART 98).

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    AppHandle, Emitter, Manager, WindowEvent,
};

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
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![emergency_stop, release_lockdown])
        .setup(|app| {
            let show = MenuItem::with_id(app, "show", "Open Thursday", true, None::<&str>)?;
            let stop = MenuItem::with_id(app, "stop", "Stop everything", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &stop, &quit])?;

            TrayIconBuilder::new()
                .icon(app.default_window_icon().unwrap().clone())
                .menu(&menu)
                .tooltip("Thursday")
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => show_window(app),
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
            // Closing the window hides it rather than killing the process: a task Thursday
            // is running does not stop because the owner cleared their screen. Quitting is
            // an explicit choice, from the tray.
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .run(tauri::generate_context!())
        .expect("Thursday failed to start");
}
