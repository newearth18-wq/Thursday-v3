/**
 * Where Thursday's API lives, from the window's point of view.
 *
 * In dev the Vite server proxies `/api`, so a relative URL is right and avoids CORS
 * entirely. In the packaged app the page is served from `tauri://localhost`, where a
 * relative URL would resolve to the bundle rather than the API — so it is addressed
 * directly. Getting this wrong produces an app that works all through development and
 * fails the moment it is packaged, so it is decided once, here.
 *
 * Sprint 84 added a third case: a Tauri build with no local backend to default to at all.
 * A phone is a screen onto a Thursday running somewhere else, never the machine it runs on
 * (ADR 0057) — so `serverOverride()` is the address a person typed in, and it takes
 * priority over the desktop default everywhere a Tauri build runs. Desktop never needs it:
 * Sprint 83's sidecar guarantees 127.0.0.1:8000 before this window is ever shown, and
 * nothing here checks *which* Tauri build this is to decide whether to offer it — the
 * connect screen that writes it is only ever reached by a real, observed connection
 * failure with nothing stored yet (`hooks/useRealtime.ts`), never by asking the platform.
 */

const OVERRIDE_KEY = "thursday.server";
const TOKEN_KEY = "thursday.token";

/** `host[:port]`, no scheme — what the connect screen collects and stores. */
function readOverride(): string | null {
  try {
    return localStorage.getItem(OVERRIDE_KEY);
  } catch {
    // Private browsing, or storage blocked entirely: treated as "nothing stored" rather
    // than thrown, so a build without storage still renders — it just asks again next time.
    return null;
  }
}

export function serverOverride(): string | null {
  return readOverride();
}

/** Accepts what a person typed — with or without a scheme — and normalises it to one. */
export function setServerOverride(address: string): void {
  const trimmed = address.trim();
  const withScheme = /^https?:\/\//.test(trimmed)
    ? trimmed
    : `http://${trimmed}`;
  try {
    localStorage.setItem(OVERRIDE_KEY, withScheme.replace(/\/+$/, ""));
  } catch {
    // Nothing to fall back to: the address will not survive a reload in this browser.
    // Letting the call succeed silently is still better than throwing out of a settings
    // screen over a storage quirk the person cannot do anything about.
  }
}

/**
 * The owner's API token (ADR 0073), if this install was given one.
 *
 * Stored beside the address because they are one decision: a Thursday reachable from another
 * machine needs a token, and a Thursday on this machine needs neither. Kept out of the URL —
 * a token in a query string is a token in every log on the way.
 */
export function apiToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setApiToken(token: string): void {
  try {
    const trimmed = token.trim();
    if (trimmed) localStorage.setItem(TOKEN_KEY, trimmed);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    // Same as the address: nothing to fall back to, and throwing out of a settings screen
    // over a storage quirk helps nobody.
  }
}

export function clearServerOverride(): void {
  try {
    localStorage.removeItem(OVERRIDE_KEY);
  } catch {
    // Nothing was going to persist anyway.
  }
}

const TAURI = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

/** Absolute under Tauri (an override if one is stored, else the desktop default); empty
 * in a plain browser, where a relative URL is already correct. */
export const API_ORIGIN = TAURI
  ? (readOverride() ??
    (import.meta.env.VITE_THURSDAY_API as string | undefined) ??
    "http://127.0.0.1:8000")
  : "";

export const WS_ORIGIN = API_ORIGIN
  ? API_ORIGIN.replace(/^http/, "ws")
  : `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}`;

export const IS_TAURI = TAURI;

/**
 * What a browser can put in a WebSocket handshake, which is not a header (ADR 0073). Empty
 * when there is no token, because offering a subprotocol the server does not know would fail
 * the handshake on a deployment that needs no token at all.
 */
export function wsProtocols(): string[] {
  const token = apiToken();
  return token ? [`thursday.token.${token}`] : [];
}
