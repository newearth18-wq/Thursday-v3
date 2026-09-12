import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LearningCenter } from "@/components/LearningCenter";
import { api } from "@/lib/api";

/**
 * A Learning Center is one "mark as complete" button away from being a record of what the
 * owner clicked rather than of what their machine can do. These tests are mostly about the
 * buttons that are not here.
 */

const CENTRE = {
  summary: "ตอนนี้ผมช่วยคุณได้หลัก ๆ 5 ด้านครับ",
  areas: [
    {
      area: "BASICS",
      title: "เริ่มต้นใช้งาน",
      features: ["พูดคุยกับ Thursday"],
      example: "ช่วยสรุปเรื่องนี้ให้หน่อย",
    },
  ],
  path: [
    {
      stage: "START",
      title: "เริ่มต้น",
      lessons: [
        { id: "say-something", name: "พูดกับ Thursday", minutes: 1, done: false, available: true },
        {
          id: "open-an-app",
          name: "เปิดโปรแกรม",
          minutes: 1,
          done: false,
          available: false,
          reason: "ยังไม่มีเครื่องที่เชื่อมต่ออยู่",
        },
      ],
    },
  ],
  progress: { verbosity: "BEGINNER", teaching: "NORMAL", used: [], tutorials_completed: [] },
  next: {
    id: "say-something",
    name: "พูดกับ Thursday",
    stage_title: "เริ่มต้น",
    minutes: 1,
    reason: "บอกสิ่งที่ต้องการด้วยภาษาปกติ",
  },
  practice: [
    {
      practice: true,
      action: "file.delete",
      would: "ย้ายไฟล์ที่เลือกไปถังขยะ",
      decision: "ASK_ALWAYS",
      why: "งานแบบนี้ผมจะถามคุณก่อนทุกครั้ง",
      risk: "มีผลมาก ควรอ่านก่อนกด",
      reversible: false,
    },
  ],
};

function step(over: Record<string, unknown> = {}) {
  return {
    lesson: "say-something",
    step: "talk",
    passed: false,
    message: "ลองบอกผมด้วยภาษาปกติว่าอยากให้ช่วยอะไร",
    done: false,
    next: { show: "ลองบอกผมด้วยภาษาปกติว่าอยากให้ช่วยอะไร", try: "สวัสดี Thursday", points_at: "" },
    ...over,
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "learn").mockResolvedValue(CENTRE as never);
  vi.spyOn(api, "startLesson").mockResolvedValue(step() as never);
  vi.spyOn(api, "attemptLesson").mockResolvedValue(step() as never);
  vi.spyOn(api, "skipLesson").mockResolvedValue(step({ done: false }) as never);
});

describe("what it will not let the owner do", () => {
  it("has no control that marks a lesson complete", async () => {
    render(<LearningCenter />);
    fireEvent.click(await screen.findByRole("button", { name: "พูดกับ Thursday" }));
    await screen.findByText("“สวัสดี Thursday”");

    const labels = screen.getAllByRole("button").map((b) => b.textContent ?? "");
    expect(labels).not.toContain("เสร็จแล้ว");
    expect(labels.some((l) => l.includes("ทำเครื่องหมาย"))).toBe(false);
    // The only way to advance is to ask the server to look.
    expect(labels).toContain("ตรวจว่าทำได้แล้ว");
  });

  it("shows no score and no points — §10 is explicit about that", async () => {
    render(<LearningCenter />);
    await screen.findByText(CENTRE.summary);
    expect(document.body.textContent).not.toMatch(/คะแนน|แต้ม|[0-9]+\s*\/\s*[0-9]+/);
  });

  it("cannot start a lesson this machine cannot run, and says why", async () => {
    render(<LearningCenter />);
    const blocked = await screen.findByRole("button", { name: "เปิดโปรแกรม" });
    expect(blocked).toBeDisabled();
    expect(screen.getByText("ยังไม่มีเครื่องที่เชื่อมต่ออยู่")).toBeInTheDocument();
  });
});

describe("done comes from the server or not at all", () => {
  it("draws a lesson as finished only when the payload says so", async () => {
    const finished = {
      ...CENTRE,
      path: [
        {
          ...CENTRE.path[0],
          lessons: [{ ...CENTRE.path[0].lessons[0], done: true }, CENTRE.path[0].lessons[1]],
        },
      ],
    };
    vi.spyOn(api, "learn").mockResolvedValue(finished as never);
    render(<LearningCenter />);
    await screen.findByRole("button", { name: "พูดกับ Thursday" });
    expect(screen.getByText("✓")).toBeInTheDocument();
  });

  it("an attempt the server rejects leaves the lesson unfinished", async () => {
    render(<LearningCenter />);
    fireEvent.click(await screen.findByRole("button", { name: "พูดกับ Thursday" }));
    fireEvent.click(await screen.findByRole("button", { name: "ตรวจว่าทำได้แล้ว" }));

    await waitFor(() => expect(api.attemptLesson).toHaveBeenCalled());
    expect(screen.queryByText("เรียบร้อย")).not.toBeInTheDocument();
  });

  it("says so when the server reports the step passed", async () => {
    vi.spyOn(api, "attemptLesson").mockResolvedValue(
      step({ passed: true, done: true, message: "แบบนี้เลยครับ" }) as never,
    );
    render(<LearningCenter />);
    fireEvent.click(await screen.findByRole("button", { name: "พูดกับ Thursday" }));
    fireEvent.click(await screen.findByRole("button", { name: "ตรวจว่าทำได้แล้ว" }));

    expect(await screen.findByText("เรียบร้อย")).toBeInTheDocument();
    expect(screen.getByText("แบบนี้เลยครับ")).toBeInTheDocument();
  });
});

describe("the attempt carries evidence, not a verdict", () => {
  it("sends what Thursday actually said, when there is something", async () => {
    render(<LearningCenter lastReply="สวัสดีครับ มีอะไรให้ช่วยไหม" />);
    fireEvent.click(await screen.findByRole("button", { name: "พูดกับ Thursday" }));
    fireEvent.click(await screen.findByRole("button", { name: "ตรวจว่าทำได้แล้ว" }));

    await waitFor(() =>
      expect(api.attemptLesson).toHaveBeenCalledWith("say-something", {
        reply: "สวัสดีครับ มีอะไรให้ช่วยไหม",
      }),
    );
  });

  it("sends nothing rather than inventing evidence when there is no reply yet", async () => {
    render(<LearningCenter />);
    fireEvent.click(await screen.findByRole("button", { name: "พูดกับ Thursday" }));
    fireEvent.click(await screen.findByRole("button", { name: "ตรวจว่าทำได้แล้ว" }));

    await waitFor(() => expect(api.attemptLesson).toHaveBeenCalledWith("say-something", null));
  });
});

describe("skipping", () => {
  it("is offered, and closes the lesson without claiming it was done", async () => {
    render(<LearningCenter />);
    fireEvent.click(await screen.findByRole("button", { name: "พูดกับ Thursday" }));
    fireEvent.click(await screen.findByRole("button", { name: "ข้ามก่อน" }));

    await waitFor(() => expect(api.skipLesson).toHaveBeenCalledWith("say-something"));
    await waitFor(() => expect(screen.queryByText("เรียบร้อย")).not.toBeInTheDocument());
  });
});

describe("practice offers", () => {
  it("are labelled as practice and carry the real policy decision", async () => {
    render(<LearningCenter />);
    await screen.findByText("file.delete");

    expect(screen.getByText("ซ้อม")).toBeInTheDocument();
    expect(screen.getByText("ASK_ALWAYS")).toBeInTheDocument();
    expect(screen.getByText("งานแบบนี้ผมจะถามคุณก่อนทุกครั้ง")).toBeInTheDocument();
  });

  it("warn about an action that cannot be undone", async () => {
    render(<LearningCenter />);
    expect(await screen.findByText("มีผลมาก ควรอ่านก่อนกด")).toBeInTheDocument();
  });

  it("offer no way to actually perform the action", async () => {
    // Practice mode has no execution path (§23). A button here would be the execution path.
    render(<LearningCenter />);
    await screen.findByText("file.delete");
    const labels = screen.getAllByRole("button").map((b) => b.textContent ?? "");
    expect(labels.some((l) => l.includes("ลบ") || l.includes("ทำเลย"))).toBe(false);
  });
});

describe("the suggestion", () => {
  it("offers one next thing with the reason, not a wall of features", async () => {
    render(<LearningCenter />);
    expect(await screen.findByText("บอกสิ่งที่ต้องการด้วยภาษาปกติ")).toBeInTheDocument();
    expect(screen.getByText("เริ่มต้น · 1 นาที")).toBeInTheDocument();
  });
});

describe("the suggestion and the path are the same lesson, told apart", () => {
  it("names the shortcut distinctly so a screen reader is not told twice", async () => {
    // Both start the same lesson; identical accessible names made one of them unusable.
    render(<LearningCenter />);
    expect(
      await screen.findByRole("button", { name: "เริ่มบทเรียนที่แนะนำ: พูดกับ Thursday" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "พูดกับ Thursday" })).toBeInTheDocument();
  });
});

describe("the arrow points at a real control, or says it cannot", () => {
  it("tells the owner when the control is not on this screen", async () => {
    // jsdom measures every box as zero, which is exactly the "not on screen" case: a closed
    // drawer, a conditional button, a control renamed since the lesson was written.
    vi.spyOn(api, "startLesson").mockResolvedValue(
      step({ next: { show: "ลองพิมพ์ดูครับ", try: "สวัสดี", points_at: "conversation-input" } }) as never,
    );
    render(<LearningCenter />);
    fireEvent.click(await screen.findByRole("button", { name: "พูดกับ Thursday" }));

    expect(await screen.findByText(/ยังไม่อยู่บนหน้าจอ/)).toBeInTheDocument();
    expect(screen.queryByTestId("spotlight")).not.toBeInTheDocument();
  });

  it("draws nothing and warns about nothing when a step is about words", async () => {
    // Empty `points_at` is the honest answer for a step with no place to point at. It must
    // not become an arrow somewhere plausible, nor a warning about a missing control.
    vi.spyOn(api, "startLesson").mockResolvedValue(
      step({ next: { show: "บอกให้ผมจำอะไรก็ได้", try: "จำไว้ว่า…", points_at: "" } }) as never,
    );
    render(<LearningCenter />);
    fireEvent.click(await screen.findByRole("button", { name: "พูดกับ Thursday" }));

    await screen.findByText("บอกให้ผมจำอะไรก็ได้");
    expect(screen.queryByTestId("spotlight")).not.toBeInTheDocument();
    expect(screen.queryByText(/ยังไม่อยู่บนหน้าจอ/)).not.toBeInTheDocument();
  });
});
