import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ApprovalDialog } from "@/components/ApprovalDialog";
import type { Approval } from "@/lib/types";

const approval: Approval = {
  id: "a1",
  action: "app.open",
  agent: "ResearchAgent",
  resource: "chrome",
  risk: "LOW",
  reversible: true,
  expected_outcome: "เปิด Chrome ให้",
  consequence_of_refusal: "จะไม่เปิดให้",
  scopes_offered: ["once"],
};

describe("the approval dialog", () => {
  it("does not name the class that asked", () => {
    /**
     * §38 lists what a person needs in order to decide: the action, what it touches, which
     * machine, what will happen, what happens if they refuse, and whether it can be undone.
     * Which internal class asked is not on that list, and printing it is the same leak
     * Sprint 65 forbade.
     */
    render(<ApprovalDialog approval={approval} onResolved={() => undefined} />);
    expect(document.body.textContent).not.toContain("ResearchAgent");
  });

  it("still shows everything the decision needs", () => {
    render(<ApprovalDialog approval={approval} onResolved={() => undefined} />);
    expect(screen.getByText("เปิด Chrome ให้")).toBeInTheDocument();
    expect(screen.getByText("จะไม่เปิดให้")).toBeInTheDocument();
    expect(screen.getByText("app.open")).toBeInTheDocument();
  });

  it("does not invent an always-allow the engine would refuse to remember", () => {
    // ADR 0008, already true and worth pinning: ASK_ALWAYS offers only ONCE.
    render(<ApprovalDialog approval={approval} onResolved={() => undefined} />);
    expect(document.body.textContent?.toLowerCase()).not.toContain("always");
  });
});
