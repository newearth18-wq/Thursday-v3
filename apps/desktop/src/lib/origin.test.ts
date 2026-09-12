import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  apiToken,
  clearServerOverride,
  serverOverride,
  setApiToken,
  setServerOverride,
  wsProtocols,
} from "@/lib/origin";

/**
 * The part of origin.ts that can be exercised without a real Tauri runtime: what gets
 * written to storage and read back. `API_ORIGIN`/`WS_ORIGIN` themselves are computed once
 * at module load from `window.__TAURI_INTERNALS__`, which does not exist under vitest's
 * jsdom — they are covered by ServerConnect.test.tsx and the manual verification in ADR
 * 0057 instead, not re-derived here.
 */
describe("the server override", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => localStorage.clear());

  it("is nothing until someone sets it", () => {
    expect(serverOverride()).toBeNull();
  });

  it("remembers what was set", () => {
    setServerOverride("192.168.1.42:8000");
    expect(serverOverride()).toBe("http://192.168.1.42:8000");
  });

  it("adds a scheme when none was typed", () => {
    setServerOverride("thursday.local:8000");
    expect(serverOverride()).toMatch(/^http:\/\//);
  });

  it("keeps an explicit scheme rather than doubling it", () => {
    setServerOverride("https://thursday.example:8443");
    expect(serverOverride()).toBe("https://thursday.example:8443");
  });

  it("trims a trailing slash, so it composes with /api/v1 cleanly", () => {
    setServerOverride("192.168.1.42:8000/");
    expect(serverOverride()).toBe("http://192.168.1.42:8000");
  });

  it("trims what a person typed around the edges", () => {
    setServerOverride("  192.168.1.42:8000  ");
    expect(serverOverride()).toBe("http://192.168.1.42:8000");
  });

  it("forgets on request", () => {
    setServerOverride("192.168.1.42:8000");
    clearServerOverride();
    expect(serverOverride()).toBeNull();
  });

  it("does not throw when storage is unavailable", () => {
    const original = Object.getOwnPropertyDescriptor(window, "localStorage");
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      get() {
        throw new DOMException("blocked", "SecurityError");
      },
    });
    try {
      expect(() => setServerOverride("192.168.1.42:8000")).not.toThrow();
      expect(serverOverride()).toBeNull();
    } finally {
      if (original) Object.defineProperty(window, "localStorage", original);
    }
  });
});

// ADR 0073 — the token the owner's Thursday was given, if it has one.

describe("the API token", () => {
  beforeEach(() => localStorage.clear());

  it("is empty when this Thursday never needed one", () => {
    expect(apiToken()).toBe("");
    expect(wsProtocols()).toEqual([]);
  });

  it("is stored and read back", () => {
    setApiToken("  a-token  ");
    expect(apiToken()).toBe("a-token");
  });

  it("offers no subprotocol when there is nothing to carry", () => {
    // A handshake that offers a subprotocol the server does not know fails. A Thursday on
    // this machine needs no token, so the list has to be empty rather than carry an empty one.
    setApiToken("");
    expect(wsProtocols()).toEqual([]);
  });

  it("carries the token where a browser is allowed to put it", () => {
    setApiToken("a-token");
    expect(wsProtocols()).toEqual(["thursday.token.a-token"]);
  });

  it("is cleared by storing nothing", () => {
    setApiToken("a-token");
    setApiToken("   ");
    expect(apiToken()).toBe("");
  });
});
