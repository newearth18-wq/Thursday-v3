import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Phone } from "@/components/Phone";
import { api } from "@/lib/api";
import type { Approval, Device, Task } from "@/lib/types";

/**
 * The phone is a remote and an approval surface. These tests are mostly about the approval
 * half, because that is where a phone interface can do real damage: by making a durable
 * decision easy, by shortening the sentence the owner needed to read, or by not saying which
 * machine the action lands on.
 */

const DEVICE: Device = {
  id: "d1",
  name: "Office-PC",
  kind: "desktop",
  os: "Windows",
  status: "online",
  capabilities: { granted: [] },
};

const TASK: Task = { id: "t1", title: "สรุปไฟล์คะแนน", status: "RUNNING", progress: 0.4 };

const LONG_CONSEQUENCE =
  "ถ้าปฏิเสธ ไฟล์ทั้ง 42 ไฟล์จะยังอยู่ที่เดิม และงานสรุปที่ค้างอยู่จะหยุดตรงนี้ " +
  "คุณสั่งใหม่ได้ทีหลังโดยไม่ต้องเริ่มจากศูนย์ เพราะผลลัพธ์ที่ทำไปแล้วถูกเก็บไว้";

function approval(over: Partial<Approval> = {}): Approval {
  return {
    id: "a1",
    action: "file.delete",
    resource: "C:\\Users\\owner\\Downloads\\*.tmp",
    device_name: "Office-PC",
    risk: "HIGH",
    reversible: false,
    expected_outcome: "ย้ายไฟล์ชั่วคราว 42 ไฟล์ไปถังขยะ",
    consequence_of_refusal: LONG_CONSEQUENCE,
    scopes_offered: ["once", "always"],
    ...over,
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "devices").mockResolvedValue({ devices: [DEVICE] } as never);
  vi.spyOn(api, "tasks").mockResolvedValue({ tasks: [TASK] } as never);
  vi.spyOn(api, "approvals").mockResolvedValue({ approvals: [approval()] } as never);
});

describe("a phone may approve but may not grant standing permission", () => {
  it("never offers 'always allow', even when the engine did", async () => {
    render(<Phone connected />);
    await screen.findByText("file.delete");

    const labels = screen.getAllByRole("button").map((b) => b.textContent ?? "");
    expect(labels).toContain("อนุมัติครั้งนี้");
    expect(labels).toContain("ปฏิเสธ");
    expect(labels.some((l) => l.includes("ตลอด") || l.includes("เสมอ"))).toBe(false);
  });

  it("says why the control is missing rather than leaving it to be noticed", async () => {
    // A silently absent control teaches nothing and reads as a broken app.
    render(<Phone connected />);
    expect(await screen.findByText(/อนุมัติได้ครั้งเดียวเท่านั้น/)).toBeInTheDocument();
  });

  it("approves with the one-time scope and nothing else", async () => {
    const approve = vi.spyOn(api, "approve").mockResolvedValue({} as never);
    render(<Phone connected />);
    fireEvent.click(await screen.findByRole("button", { name: "อนุมัติครั้งนี้" }));

    await waitFor(() => expect(approve).toHaveBeenCalledWith("a1", "once"));
  });

  it("rejects without a scope at all", async () => {
    const reject = vi.spyOn(api, "reject").mockResolvedValue({} as never);
    render(<Phone connected />);
    fireEvent.click(await screen.findByRole("button", { name: "ปฏิเสธ" }));

    await waitFor(() => expect(reject).toHaveBeenCalledWith("a1"));
  });
});

describe("what the owner is shown before deciding", () => {
  it("shows the consequence in full, not shortened", async () => {
    // A truncated consequence is the version that actually gets read, which makes truncating
    // it a decision about what the owner is allowed to know.
    render(<Phone connected />);
    expect(await screen.findByText(LONG_CONSEQUENCE)).toBeInTheDocument();
  });

  it("names the machine the action would run on", async () => {
    // At a desk the owner is at the machine. Here they are not.
    render(<Phone connected />);
    await screen.findByText("file.delete");
    expect(screen.getByText("บนเครื่อง").textContent).toContain("บนเครื่อง");
    expect(within(screen.getByText("บนเครื่อง")).getByText("Office-PC")).toBeInTheDocument();
  });

  it("says so when the machine is not stated, rather than showing a dash", async () => {
    vi.spyOn(api, "approvals").mockResolvedValue({
      approvals: [approval({ device_name: null })],
    } as never);
    render(<Phone connected />);
    expect(await screen.findByText("ไม่ได้ระบุเครื่อง")).toBeInTheDocument();
  });

  it("marks an action that cannot be undone", async () => {
    render(<Phone connected />);
    expect(await screen.findByText("ย้อนกลับไม่ได้")).toBeInTheDocument();
  });

  it("does not mark a reversible one", async () => {
    vi.spyOn(api, "approvals").mockResolvedValue({
      approvals: [approval({ reversible: true })],
    } as never);
    render(<Phone connected />);
    await screen.findByText("file.delete");
    expect(screen.queryByText("ย้อนกลับไม่ได้")).not.toBeInTheDocument();
  });
});

describe("what is happening", () => {
  it("shows each machine and whether it is on", async () => {
    render(<Phone connected />);
    // Scoped to the device list: the name also appears on the approval, which is the point
    // of that line and not what this test is about.
    const list = within(await screen.findByTestId("devices"));
    expect(list.getByText("Office-PC")).toBeInTheDocument();
    expect(list.getByText("เปิดอยู่")).toBeInTheDocument();
  });

  it("shows running work with its progress", async () => {
    render(<Phone connected />);
    expect(await screen.findByText("สรุปไฟล์คะแนน")).toBeInTheDocument();
    expect(screen.getByText("40%")).toBeInTheDocument();
  });

  it("leaves finished work off rather than listing everything", async () => {
    vi.spyOn(api, "tasks").mockResolvedValue({
      tasks: [TASK, { id: "t2", title: "งานเก่า", status: "COMPLETED", progress: 1 }],
    } as never);
    render(<Phone connected />);
    await screen.findByText("สรุปไฟล์คะแนน");
    expect(screen.queryByText("งานเก่า")).not.toBeInTheDocument();
  });

  it("says there is nothing rather than showing an empty list", async () => {
    vi.spyOn(api, "tasks").mockResolvedValue({ tasks: [] } as never);
    vi.spyOn(api, "devices").mockResolvedValue({ devices: [] } as never);
    render(<Phone connected />);
    expect(await screen.findByText("ตอนนี้ไม่มีงานค้างอยู่")).toBeInTheDocument();
    expect(screen.getByText("ยังไม่มีเครื่องที่จับคู่ไว้")).toBeInTheDocument();
  });

  it("says when it is not connected rather than showing stale state as current", async () => {
    render(<Phone connected={false} />);
    expect(await screen.findByText("ยังไม่ได้เชื่อมต่อ")).toBeInTheDocument();
  });
});

describe("what a phone may do to a machine", () => {
  it("offers lock and wake", async () => {
    render(<Phone connected />);
    expect(await screen.findByRole("button", { name: "ล็อกหน้าจอ" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "ปลุกเครื่อง" })).toBeEnabled();
  });

  it("shows shutting down as refused, with the reason, rather than hiding it", async () => {
    // A control that is silently absent teaches nothing — the same choice the workflow
    // builder makes for a trigger with no runner.
    render(<Phone connected />);
    expect(await screen.findByRole("button", { name: "ปิดเครื่อง" })).toBeDisabled();
    expect(screen.getByText(/ยังไม่ได้บันทึกจะหาย/)).toBeInTheDocument();
  });

  it("sends the action and says it is done when it ran", async () => {
    const act = vi.spyOn(api, "deviceAction").mockResolvedValue({ verified: true } as never);
    render(<Phone connected />);
    fireEvent.click(await screen.findByRole("button", { name: "ล็อกหน้าจอ" }));

    await waitFor(() => expect(act).toHaveBeenCalledWith("d1", "system.lock", {}, "ล็อกหน้าจอ"));
    expect(await screen.findByText(/เรียบร้อย/)).toBeInTheDocument();
  });

  it("says an action is waiting on an answer rather than claiming it ran", async () => {
    // The engine said "ask the owner", so the action comes back as an approval at the top
    // of this same screen. Reporting it as done would be the one lie that matters here.
    vi.spyOn(api, "deviceAction").mockResolvedValue({
      approval_id: "ap-9",
      decision: "ASK_ONCE",
      ran: false,
    } as never);
    render(<Phone connected />);
    fireEvent.click(await screen.findByRole("button", { name: "ล็อกหน้าจอ" }));

    expect(await screen.findByText(/รออนุมัติอยู่ด้านบน/)).toBeInTheDocument();
    expect(screen.queryByText(/เรียบร้อย/)).not.toBeInTheDocument();
  });
});
