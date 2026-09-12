import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { WorkflowBuilder } from "@/components/WorkflowBuilder";
import { api } from "@/lib/api";

/**
 * The half of a workflow builder that can be wrong is not the diagram.
 *
 * A canvas either draws what the engine runs or it does not, and the way it fails is by
 * showing a rule that looks armed and is not, an action that looks allowed and is blocked,
 * or a handler that is not wired up and quietly does nothing. Each of those is a claim made
 * to the owner in pixels, so each is asserted here.
 */

const CATALOGUE = {
  triggers: [
    { kind: "schedule", unavailable: "" },
    { kind: "event", unavailable: "" },
    { kind: "manual", unavailable: "" },
    { kind: "state_change", unavailable: "ยังไม่มีตัวเฝ้าดูสถานะ" },
  ],
  conditions: ["eq", "contains"],
  actions: [
    { kind: "notify", needs: "", unavailable: "" },
    { kind: "tool", needs: "executor", unavailable: "ยังไม่ได้ต่อ executor" },
  ],
  tools: [{ name: "file.read", level: "READ", decision: "AUTO", blocked: false }],
};

const STORED = {
  automation_id: "rule-1",
  name: "สรุปเช้า",
  nodes: [
    { id: "t", kind: "trigger" as const, subkind: "schedule", config: { cron: "30 7 * * 1-5" }, x: 40, y: 40 },
    { id: "a", kind: "action" as const, subkind: "notify", config: { title: "สรุป" }, x: 600, y: 40 },
  ],
  order: ["a"],
  follow_up_from: null,
  enabled: false,
  created_by: "user",
  run_count: 0,
  last_run_at: null,
  explanation: "07:30 วันจันทร์ถึงศุกร์ แล้ว แจ้งเตือน",
};

function report(over: Record<string, unknown> = {}) {
  return {
    valid: true,
    problems: [],
    consequences: [],
    explanation: "07:30 วันจันทร์ถึงศุกร์ แล้ว แจ้งเตือน",
    ...over,
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "automations").mockResolvedValue({ automations: [STORED] } as never);
  vi.spyOn(api, "automationCatalogue").mockResolvedValue(CATALOGUE as never);
  vi.spyOn(api, "previewWorkflow").mockResolvedValue(report() as never);
  vi.spyOn(api, "describeCron").mockResolvedValue({ reads_as: "", valid: true } as never);
});

describe("what the owner is told before anything runs", () => {
  it("shows a saved rule's schedule in words, not as a cron expression", async () => {
    render(<WorkflowBuilder />);
    expect(await screen.findByText("07:30 วันจันทร์ถึงศุกร์ แล้ว แจ้งเตือน")).toBeInTheDocument();
  });

  it("says plainly whether a saved rule is running", async () => {
    render(<WorkflowBuilder />);
    expect(await screen.findByText("ปิดอยู่")).toBeInTheDocument();
  });

  it("offers a trigger nothing runs as disabled, with the reason on it", async () => {
    // Dragging on a schedule that never fires is the failure this builder exists to avoid,
    // so the button says no rather than the rule saying nothing.
    render(<WorkflowBuilder />);
    const dead = await screen.findByRole("button", { name: "เมื่อสถานะเปลี่ยน" });
    expect(dead).toBeDisabled();
    expect(dead).toHaveAttribute("title", "ยังไม่มีตัวเฝ้าดูสถานะ");
  });

  it("marks an action whose handler is not wired rather than letting it look fine", async () => {
    render(<WorkflowBuilder />);
    const unwired = await screen.findByRole("button", { name: "+ ใช้เครื่องมือ" });
    expect(unwired).toHaveAttribute("title", "ยังไม่ได้ต่อ executor");
  });
});

describe("the permission consequence is on the canvas", () => {
  it("shows what each action would be allowed to do, from the server", async () => {
    vi.spyOn(api, "previewWorkflow").mockResolvedValue(
      report({
        consequences: [
          {
            node: "action-1",
            kind: "tool",
            action: "file.delete",
            level: "MODIFY",
            decision: "ASK_ALWAYS",
            blocked: false,
            unavailable: "",
          },
        ],
      }) as never,
    );
    render(<WorkflowBuilder />);
    fireEvent.click(await screen.findByRole("button", { name: "+ ใช้เครื่องมือ" }));

    expect(await screen.findByText("ASK_ALWAYS")).toBeInTheDocument();
    expect(screen.getByText("file.delete")).toBeInTheDocument();
  });

  it("says a blocked verb is blocked, in words rather than a policy name", async () => {
    vi.spyOn(api, "previewWorkflow").mockResolvedValue(
      report({
        valid: false,
        consequences: [
          {
            node: "action-1",
            kind: "tool",
            action: "security.disable",
            level: "ADMIN",
            decision: "BLOCK",
            blocked: true,
            unavailable: "",
          },
        ],
      }) as never,
    );
    render(<WorkflowBuilder />);
    fireEvent.click(await screen.findByRole("button", { name: "+ ใช้เครื่องมือ" }));

    expect(await screen.findByText("ห้ามถาวร")).toBeInTheDocument();
  });
});

describe("saving is not arming", () => {
  it("will not save a graph the server would refuse", async () => {
    render(<WorkflowBuilder />);
    // Nothing on the canvas, no name: the save button is off rather than the server
    // deciding after the owner has committed.
    expect(await screen.findByRole("button", { name: "บันทึก" })).toBeDisabled();
  });

  it("says out loud that a saved rule is not yet running", async () => {
    const save = vi
      .spyOn(api, "saveWorkflow")
      .mockResolvedValue({ ...report(), id: "rule-2", enabled: false } as never);
    render(<WorkflowBuilder />);

    fireEvent.change(await screen.findByLabelText("ชื่อกฎ"), { target: { value: "กฎใหม่" } });
    fireEvent.click(screen.getByRole("button", { name: "ตามเวลา" }));
    fireEvent.click(screen.getByRole("button", { name: "+ แจ้งเตือน" }));

    await waitFor(() => expect(screen.getByRole("button", { name: "บันทึก" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "บันทึก" }));

    expect(await screen.findByText(/ยังไม่เปิดใช้งาน/)).toBeInTheDocument();
    expect(save).toHaveBeenCalledWith(
      expect.objectContaining({ name: "กฎใหม่" }),
      undefined,
    );
  });

  it("enabling is a separate button on the saved rule", async () => {
    const enable = vi
      .spyOn(api, "enableWorkflow")
      .mockResolvedValue({ id: "rule-1", enabled: true } as never);
    render(<WorkflowBuilder />);

    fireEvent.click(await screen.findByRole("button", { name: "เปิดใช้งาน" }));
    expect(enable).toHaveBeenCalledWith("rule-1", true);
  });

  it("editing a saved rule updates it rather than making a second one", async () => {
    const save = vi
      .spyOn(api, "saveWorkflow")
      .mockResolvedValue({ ...report(), id: "rule-1", enabled: false } as never);
    render(<WorkflowBuilder />);

    fireEvent.click(await screen.findByRole("button", { name: "สรุปเช้า" }));
    await waitFor(() => expect(screen.getByLabelText("ชื่อกฎ")).toHaveValue("สรุปเช้า"));
    fireEvent.click(screen.getByRole("button", { name: "บันทึก" }));

    await waitFor(() =>
      expect(save).toHaveBeenCalledWith(expect.objectContaining({ name: "สรุปเช้า" }), "rule-1"),
    );
  });
});

describe("the server owns validation", () => {
  it("shows the server's problems rather than deciding for itself", async () => {
    vi.spyOn(api, "previewWorkflow").mockResolvedValue(
      report({
        valid: false,
        problems: [{ node: "", message: "ตารางเวลาว่างเปล่า", severity: "error" }],
      }) as never,
    );
    render(<WorkflowBuilder />);
    fireEvent.click(await screen.findByRole("button", { name: "ตามเวลา" }));

    expect(await screen.findByText("ตารางเวลาว่างเปล่า")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "บันทึก" })).toBeDisabled();
  });

  it("re-asks the server on every edit", async () => {
    const preview = vi.spyOn(api, "previewWorkflow").mockResolvedValue(report() as never);
    render(<WorkflowBuilder />);

    fireEvent.click(await screen.findByRole("button", { name: "ตามเวลา" }));
    await waitFor(() => expect(preview).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "+ แจ้งเตือน" }));
    await waitFor(() => expect(preview).toHaveBeenCalledTimes(2));
  });
});

describe("the run order is edited explicitly", () => {
  it("numbers the actions in the order they will run", async () => {
    render(<WorkflowBuilder />);
    fireEvent.click(await screen.findByRole("button", { name: "+ แจ้งเตือน" }));
    fireEvent.click(screen.getByRole("button", { name: "+ แจ้งเตือน" }));

    await waitFor(() => expect(screen.getByText("#1")).toBeInTheDocument());
    expect(screen.getByText("#2")).toBeInTheDocument();
  });

  it("moves an action with a button, never by where it was dropped", async () => {
    render(<WorkflowBuilder />);
    fireEvent.click(await screen.findByRole("button", { name: "+ แจ้งเตือน" }));
    fireEvent.click(screen.getByRole("button", { name: "+ แจ้งเตือน" }));

    fireEvent.pointerDown(screen.getByTestId("node-action-2"));
    fireEvent.click(screen.getByRole("button", { name: "ก่อนหน้า" }));

    // action-2 now runs first. The box has not moved an inch.
    await waitFor(() => expect(screen.getByTestId("node-action-2").textContent).toContain("#1"));
  });

  it("cannot move the first action earlier", async () => {
    render(<WorkflowBuilder />);
    fireEvent.click(await screen.findByRole("button", { name: "+ แจ้งเตือน" }));
    fireEvent.pointerDown(screen.getByTestId("node-action-1"));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "ก่อนหน้า" })).toBeDisabled(),
    );
    expect(screen.getByRole("button", { name: "ถัดไป" })).toBeDisabled();
  });
});

describe("the schedule reads back as it is typed", () => {
  it("asks the server what the expression means", async () => {
    const describe_ = vi
      .spyOn(api, "describeCron")
      .mockResolvedValue({ reads_as: "07:30 วันจันทร์ถึงศุกร์", valid: true } as never);
    render(<WorkflowBuilder />);

    fireEvent.click(await screen.findByRole("button", { name: "ตามเวลา" }));
    fireEvent.pointerDown(screen.getByTestId("node-trigger-1"));
    fireEvent.change(screen.getByLabelText("ตารางเวลา"), { target: { value: "30 7 * * 1-5" } });

    await waitFor(() => expect(describe_).toHaveBeenCalledWith("30 7 * * 1-5"));
    expect(await screen.findByText("07:30 วันจันทร์ถึงศุกร์")).toBeInTheDocument();
  });

  it("shows the refusal rather than a confident sentence about a bad one", async () => {
    vi.spyOn(api, "describeCron").mockResolvedValue({
      reads_as: "",
      valid: false,
      problem: "cron ต้องมี 5 ช่อง",
    } as never);
    render(<WorkflowBuilder />);

    fireEvent.click(await screen.findByRole("button", { name: "ตามเวลา" }));
    fireEvent.pointerDown(screen.getByTestId("node-trigger-1"));
    fireEvent.change(screen.getByLabelText("ตารางเวลา"), { target: { value: "0 9 * *" } });

    expect(await screen.findByText("cron ต้องมี 5 ช่อง")).toBeInTheDocument();
  });
});
