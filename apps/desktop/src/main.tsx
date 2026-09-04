import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import Avatar from "./Avatar";
import "./index.css";

/**
 * Two windows, one bundle.
 *
 * The avatar is a separate OS window rather than a corner of the main one — it has to be
 * on top of whatever the owner is actually working in, which no element inside another
 * window can be. Tauri marks that window with an initialization script; the flag is read
 * once here so both windows ship as the same build and cannot drift out of sync.
 *
 * In a plain browser `#avatar` is how the avatar is developed and screenshotted. There is
 * no such thing as "somewhere else" in a browser tab, so nothing switches to it on its own.
 */
const AVATAR =
  // Set by the avatar window's initialization script, before this bundle runs. A flag
  // rather than a URL: a hash or a query string has to survive Tauri's own URL handling
  // to arrive intact, and a window that silently loads the wrong half of the app is a
  // bug nobody sees until they run the packaged build.
  (window as unknown as { __THURSDAY_AVATAR__?: boolean }).__THURSDAY_AVATAR__ === true ||
  // …and the hash, which is how the avatar is opened in a browser during development.
  window.location.hash === "#avatar";

if (AVATAR) {
  // The window is transparent so the desktop shows through; the page must not paint over it.
  document.body.classList.remove("bg-ink-950");
  document.body.classList.add("bg-transparent");
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>{AVATAR ? <Avatar /> : <App />}</React.StrictMode>,
);
