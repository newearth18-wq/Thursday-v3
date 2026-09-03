/**
 * Where Thursday's API lives, from the window's point of view.
 *
 * In dev the Vite server proxies `/api`, so a relative URL is right and avoids CORS
 * entirely. In the packaged app the page is served from `tauri://localhost`, where a
 * relative URL would resolve to the bundle rather than the API — so it is addressed
 * directly. Getting this wrong produces an app that works all through development and
 * fails the moment it is packaged, so it is decided once, here.
 */

const TAURI = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

/** Empty in dev (proxied), absolute under Tauri. */
export const API_ORIGIN = TAURI
  ? ((import.meta.env.VITE_THURSDAY_API as string | undefined) ?? "http://127.0.0.1:8000")
  : "";

export const WS_ORIGIN = API_ORIGIN
  ? API_ORIGIN.replace(/^http/, "ws")
  : `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}`;

export const IS_TAURI = TAURI;
