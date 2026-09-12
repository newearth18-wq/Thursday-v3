import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ServerConnect } from "@/components/ServerConnect";
import { apiToken, serverOverride, setServerOverride } from "@/lib/origin";

describe("the connect screen", () => {
  beforeEach(() => {
    localStorage.clear();
    // jsdom throws "Not implemented: navigation" on a real reload; the component's own
    // behaviour after submitting is "call reload", not "actually navigate".
    vi.stubGlobal("location", { ...window.location, reload: vi.fn() });
  });
  afterEach(() => {
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it("asks where Thursday is, the first time", () => {
    render(<ServerConnect />);
    expect(screen.getByText("Where is Thursday?")).toBeInTheDocument();
  });

  it("asks differently once an address is already stored and still failing", () => {
    setServerOverride("192.168.1.42:8000");
    render(<ServerConnect />);
    expect(screen.getByText("Thursday isn't answering")).toBeInTheDocument();
    // Pre-filled, not blank — a typo should be a one-character fix, not a re-type.
    expect(screen.getByDisplayValue("192.168.1.42:8000")).toBeInTheDocument();
  });

  it("stores what was typed and reloads", () => {
    render(<ServerConnect />);
    fireEvent.change(screen.getByPlaceholderText("192.168.1.42:8000"), {
      target: { value: "10.0.0.5:8000" },
    });
    fireEvent.click(screen.getByRole("button", { name: /connect/i }));

    expect(serverOverride()).toBe("http://10.0.0.5:8000");
    expect(window.location.reload).toHaveBeenCalled();
  });

  it("does nothing on an empty submission", () => {
    render(<ServerConnect />);
    fireEvent.click(screen.getByRole("button", { name: /connect/i }));
    expect(serverOverride()).toBeNull();
    expect(window.location.reload).not.toHaveBeenCalled();
  });

  it("does not double a scheme the address already had", () => {
    render(<ServerConnect />);
    fireEvent.change(screen.getByPlaceholderText("192.168.1.42:8000"), {
      target: { value: "https://thursday.example:8443" },
    });
    fireEvent.click(screen.getByRole("button", { name: /connect/i }));
    expect(serverOverride()).toBe("https://thursday.example:8443");
  });

  it("names where the address comes from, for someone who does not know it", () => {
    render(<ServerConnect />);
    expect(screen.getByText(/tray menu/i)).toBeInTheDocument();
  });

  // ADR 0073 — a Thursday reachable from this phone is, by definition, reachable from off
  // its own machine, which is exactly the deployment that needs a token.

  it("stores the access token beside the address", () => {
    render(<ServerConnect />);
    fireEvent.change(screen.getByPlaceholderText("192.168.1.42:8000"), {
      target: { value: "10.0.0.5:8000" },
    });
    fireEvent.change(screen.getByLabelText("access token"), {
      target: { value: "an-owner-token" },
    });
    fireEvent.click(screen.getByRole("button", { name: /connect/i }));

    expect(serverOverride()).toBe("http://10.0.0.5:8000");
    expect(apiToken()).toBe("an-owner-token");
  });

  it("connects without one, because this screen is also reached on a Thursday that needs none", () => {
    render(<ServerConnect />);
    fireEvent.change(screen.getByPlaceholderText("192.168.1.42:8000"), {
      target: { value: "10.0.0.5:8000" },
    });
    fireEvent.click(screen.getByRole("button", { name: /connect/i }));

    expect(serverOverride()).toBe("http://10.0.0.5:8000");
    expect(apiToken()).toBe("");
  });

  it("does not show the token while it is typed", () => {
    render(<ServerConnect />);
    expect(screen.getByLabelText("access token")).toHaveAttribute(
      "type",
      "password",
    );
  });
});
