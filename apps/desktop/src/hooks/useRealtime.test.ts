import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useRealtime } from "@/hooks/useRealtime";
import { setServerOverride } from "@/lib/origin";

/**
 * `needsSetup` — the signal ADR 0057 argues for instead of asking what platform this is.
 * A real `WebSocket` would try to actually connect; this fakes the constructor so the test
 * controls open/close/message directly and deterministically, the same way the socket
 * itself would arrive at each state.
 */
class FakeSocket {
  static instances: FakeSocket[] = [];
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  readyState = 0;
  sent: string[] = [];

  constructor(public url: string) {
    FakeSocket.instances.push(this);
  }

  send(data: string) {
    this.sent.push(data);
  }

  close() {
    this.readyState = 3;
    this.onclose?.();
  }

  open() {
    this.readyState = 1;
    this.onopen?.();
  }

  message(payload: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(payload) });
  }
}

describe("useRealtime — needsSetup", () => {
  beforeEach(() => {
    localStorage.clear();
    FakeSocket.instances = [];
    vi.stubGlobal("WebSocket", FakeSocket as unknown as typeof WebSocket);
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  /** Fails the current socket and lets the scheduled reconnect open a fresh one. */
  const failAndReconnect = async () => {
    const current = FakeSocket.instances.at(-1)!;
    act(() => current.close());
    await act(async () => {
      await vi.runOnlyPendingTimersAsync();
    });
  };

  it("stays false while there is still hope — fewer failures than the threshold", () => {
    const { result } = renderHook(() => useRealtime());
    act(() => FakeSocket.instances[0].close());
    expect(result.current.needsSetup).toBe(false);
  });

  it("trips after enough consecutive failures, with nothing configured", async () => {
    const { result } = renderHook(() => useRealtime());
    // Three in a row is SETUP_AFTER_FAILURES; the first two must not be enough on their
    // own, or a sidecar that is merely slow (Sprint 83 can take several real seconds)
    // would be misdiagnosed as "nothing is there".
    await failAndReconnect();
    expect(result.current.needsSetup).toBe(false);
    await failAndReconnect();
    expect(result.current.needsSetup).toBe(false);
    act(() => FakeSocket.instances.at(-1)!.close());
    expect(result.current.needsSetup).toBe(true);
  });

  it("trips the same way even with an address already stored", async () => {
    // A wrong or now-unreachable stored address is not a reason to stay silent — see the
    // comment in useRealtime.ts on why this does not gate on serverOverride().
    setServerOverride("192.168.1.42:8000");
    const { result } = renderHook(() => useRealtime());
    await failAndReconnect();
    await failAndReconnect();
    act(() => FakeSocket.instances.at(-1)!.close());
    expect(result.current.needsSetup).toBe(true);
  });

  it("clears the moment a connection actually succeeds", async () => {
    const { result } = renderHook(() => useRealtime());
    await failAndReconnect();
    await failAndReconnect();
    act(() => FakeSocket.instances.at(-1)!.close());
    expect(result.current.needsSetup).toBe(true);

    await act(async () => {
      await vi.runOnlyPendingTimersAsync();
    });
    act(() => FakeSocket.instances.at(-1)!.open());
    expect(result.current.needsSetup).toBe(false);
    expect(result.current.connected).toBe(true);
  });

  it("a later success resets the count, so a blip does not carry into the next outage", async () => {
    const { result } = renderHook(() => useRealtime());
    await failAndReconnect();
    act(() => FakeSocket.instances.at(-1)!.open());
    expect(result.current.connected).toBe(true);

    act(() => FakeSocket.instances.at(-1)!.close());
    await act(async () => {
      await vi.runOnlyPendingTimersAsync();
    });
    // One failure since the reset — nowhere near the threshold.
    expect(result.current.needsSetup).toBe(false);
  });
});
