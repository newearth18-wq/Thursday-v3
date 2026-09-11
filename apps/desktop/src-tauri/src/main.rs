// Thursday's desktop entry point.
//
// Three lines, on purpose: `tauri::mobile_entry_point` (see lib.rs) requires the app to be
// built as a library that a mobile platform's own launcher calls into directly, so
// `lib.rs` is where everything actually lives. This file is what `cargo build` still needs
// for a desktop binary to exist at all.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    thursday_desktop_lib::run();
}
