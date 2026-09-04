import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AgentStrip } from "@/components/AgentStrip";
import { WORKING } from "@/lib/plain";

/**
 * The rule under test is Sprint 65's, and this strip used to break it.
 *
 * It printed `agent.name` — "ResearchAgent", "SupervisorAgent" — in a monospace font, which
 * is the exact example the requirement gives of what a normal user must never be shown. The
 * phrase was already in the same payload.
 */
describe("the working strip", () => {
  const expand = () => fireEvent.click(screen.getByRole("button"));

  it("shows what Thursday is doing, not which class is doing it", () => {
    render(
      <AgentStrip
        agents={[{ name: "ResearchAgent", activity: "กำลังค้นข้อมูล", state: "working" }]}
      />,
    );
    expand();

    expect(screen.getByText("กำลังค้นข้อมูล")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("ResearchAgent");
    expect(document.body.textContent).not.toContain("Agent");
  });

  it("falls back to the vague phrase rather than to a name", () => {
    render(<AgentStrip agents={[{ name: "VisionAgent", activity: "", state: "working" }]} />);
    expand();

    expect(screen.getByText(WORKING)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("VisionAgent");
  });

  it("stays out of the way when nothing is running", () => {
    const { container } = render(<AgentStrip agents={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
