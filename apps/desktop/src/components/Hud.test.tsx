import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Hud } from "@/components/Hud";
import type { Device, Expression } from "@/lib/types";

const expression = (over: Partial<Expression> = {}): Expression => ({
  mood: "WORKING",
  posture: "WORKING",
  listening: false,
  prop: "NONE",
  activity: "กำลังค้นข้อมูล",
  because: "กำลังทำงานให้อยู่",
  intensity: 0.6,
  running: 2,
  waiting: 1,
  unhealthy: 0,
  ...over,
});

const office: Device = {
  id: "d1",
  name: "Office-PC",
  kind: "desktop",
  os: "Linux",
  status: "online",
  capabilities: { granted: [] },
};

describe("the HUD", () => {
  it("says the server's words rather than words of its own", () => {
    render(<Hud expression={expression()} devices={[office]} connected />);

    expect(screen.getByText("กำลังทำงานให้อยู่")).toBeInTheDocument();
    expect(screen.getByText("กำลังค้นข้อมูล")).toBeInTheDocument();
  });

  it("never shows the mood's own name", () => {
    /**
     * The mood is a colour and a motion here, not a caption. "WORKING" on the screen would
     * be an internal enum leaking, and it would also be a second phrase table waiting to
     * disagree with the server's.
     */
    render(<Hud expression={expression()} devices={[office]} connected />);
    expect(document.body.textContent).not.toContain("WORKING");
  });

  it("stops reporting how Thursday is when it cannot hear Thursday", () => {
    render(<Hud expression={expression()} devices={[office]} connected={false} />);
    expect(screen.queryByText("กำลังทำงานให้อยู่")).not.toBeInTheDocument();
    expect(screen.getByText("กำลังเชื่อมต่อใหม่…")).toBeInTheDocument();
  });

  it("leaves the activity line out when nothing is running", () => {
    render(
      <Hud
        expression={expression({ activity: "", because: "ว่างอยู่ พร้อมรับงาน", running: 0 })}
        devices={[]}
        connected
      />,
    );
    expect(screen.getByText("ว่างอยู่ พร้อมรับงาน")).toBeInTheDocument();
    expect(screen.queryByText("กำลังค้นข้อมูล")).not.toBeInTheDocument();
  });

  it("shows the counts as numbers, not only as bars", () => {
    // A bar has no ceiling to be honest about: five approvals and fifty fill it the same.
    render(<Hud expression={expression({ waiting: 12 })} devices={[]} connected />);
    expect(screen.getByText("12")).toBeInTheDocument();
  });

  it("names the machines the owner named", () => {
    render(<Hud expression={expression()} devices={[office]} connected />);
    expect(screen.getByText("Office-PC")).toBeInTheDocument();
  });
});

describe("the recording indicator (§10)", () => {
  it("appears here too, so the owner learns one signal and not two", () => {
    // The avatar draws it as a red dot beside the head; this window draws it beside the
    // clock. An owner who has learned what the red dot means must not discover that it
    // only exists on the window they are not currently looking at.
    render(<Hud expression={expression({ listening: true })} devices={[]} connected />);
    expect(screen.getByText("mic")).toBeInTheDocument();
  });

  it("is not drawn when the microphone is closed", () => {
    render(<Hud expression={expression({ listening: false })} devices={[]} connected />);
    expect(screen.queryByText("mic")).not.toBeInTheDocument();
  });

  it("is not drawn while disconnected, whatever the last frame said", () => {
    // A socket that has dropped tells you nothing about the microphone now. Leaving the
    // light on from a stale frame is the one direction §10 must never fail in.
    render(<Hud expression={expression({ listening: true })} devices={[]} connected={false} />);
    expect(screen.queryByText("mic")).not.toBeInTheDocument();
  });

  it("does not change colour with the mood", () => {
    const shade = (mood: Expression["mood"]) => {
      const { container, unmount } = render(
        <Hud expression={expression({ mood, listening: true })} devices={[]} connected />,
      );
      const dot = container.querySelector('[data-listening="true"] span');
      const colour = (dot as HTMLElement).style.background;
      unmount();
      return colour;
    };

    expect(shade("FAILING")).toBe(shade("PLEASED"));
    expect(shade("STOPPED")).toBe(shade("WORKING"));
  });
});
