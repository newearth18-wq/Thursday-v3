import { describe, expect, it } from "vitest";
import { BOUNDS, build, ORBIT, step, type Live, type MindNode } from "@/lib/graph";
import type { AgentStatus, Approval, Device, Task } from "@/lib/types";

const device = (id: string, name: string): Device => ({
  id,
  name,
  kind: "desktop",
  os: "Linux",
  status: "online",
  capabilities: { granted: [] },
});

const approval = (id: string): Approval => ({
  id,
  action: "app.open",
  resource: "chrome",
  risk: "LOW",
  reversible: true,
  expected_outcome: "เปิด Chrome",
  consequence_of_refusal: "",
});

const task = (id: string, title: string): Task => ({ id, title, status: "RUNNING", progress: 0.5 });

const nothing: Live = { devices: [], agents: [], approvals: [], tasks: [] };

const settle = (nodes: MindNode[], frames: number): MindNode[] => {
  let current = nodes;
  for (let i = 0; i < frames; i += 1) current = step(current);
  return current;
};

// -------------------------------------------------------------- only real things are drawn

describe("what ends up on the graph", () => {
  it("is one node when Thursday is doing nothing", () => {
    const nodes = build([], nothing);
    expect(nodes).toHaveLength(1);
    expect(nodes[0].kind).toBe("core");
  });

  it("labels a running job with the phrase, never with the class name", () => {
    const agents: AgentStatus[] = [
      { name: "ResearchAgent", activity: "กำลังค้นข้อมูล", state: "working" },
    ];
    const labels = build([], { ...nothing, agents }).map((n) => n.label);

    expect(labels).toContain("กำลังค้นข้อมูล");
    expect(labels.join(" ")).not.toContain("ResearchAgent");
    expect(labels.join(" ")).not.toContain("Agent");
  });

  it("falls back to the vague phrase rather than to a name", () => {
    const agents: AgentStatus[] = [{ name: "VisionAgent", activity: "", state: "working" }];
    expect(build([], { ...nothing, agents }).map((n) => n.label).join(" ")).not.toContain(
      "VisionAgent",
    );
  });

  it("does not draw an agent that has already finished", () => {
    const agents: AgentStatus[] = [
      { name: "ResearchAgent", activity: "กำลังค้นข้อมูล", state: "completed" },
    ];
    expect(build([], { ...nothing, agents })).toHaveLength(1);
  });

  it("draws a node for each real thing and nothing else", () => {
    const live: Live = {
      devices: [device("d1", "Office-PC")],
      agents: [{ name: "A", activity: "กำลังค้นข้อมูล", state: "working" }],
      approvals: [approval("a1")],
      tasks: [task("t1", "หาไฟล์งบประมาณ")],
    };
    const nodes = build([], live);

    expect(nodes).toHaveLength(5); // the core plus one of each
    expect(nodes.filter((n) => n.kind === "device")).toHaveLength(1);
    expect(nodes.map((n) => n.label)).toContain("Office-PC");
    expect(nodes.map((n) => n.label)).toContain("หาไฟล์งบประมาณ");
  });
});

describe("rebuilding", () => {
  it("keeps where a node already was, so the picture does not jump every poll", () => {
    const live: Live = { ...nothing, devices: [device("d1", "Office-PC")] };
    const settled = settle(build([], live), 120);
    const again = build(settled, live);

    const before = settled.find((n) => n.id === "device:d1")!;
    const after = again.find((n) => n.id === "device:d1")!;
    expect(after.x).toBe(before.x);
    expect(after.y).toBe(before.y);
  });

  it("drops a node whose thing is gone", () => {
    const live: Live = { ...nothing, devices: [device("d1", "Office-PC")] };
    const withOne = build([], live);
    expect(build(withOne, nothing).map((n) => n.id)).toEqual(["core"]);
  });

  it("renames in place rather than respawning", () => {
    const first = settle(build([], { ...nothing, devices: [device("d1", "Office-PC")] }), 60);
    const renamed = build(first, { ...nothing, devices: [device("d1", "Studio")] });
    expect(renamed.find((n) => n.id === "device:d1")!.label).toBe("Studio");
    expect(renamed.find((n) => n.id === "device:d1")!.x).toBe(
      first.find((n) => n.id === "device:d1")!.x,
    );
  });

  it("never starts a node on top of the core", () => {
    const live: Live = {
      ...nothing,
      devices: Array.from({ length: 8 }, (_, i) => device(`d${i}`, `machine ${i}`)),
    };
    for (const node of build([], live)) {
      if (node.kind === "core") continue;
      expect(Math.hypot(node.x, node.y)).toBeGreaterThan(1);
    }
  });
});

// ------------------------------------------------------------------------ the simulation

describe("the layout", () => {
  const crowd: Live = {
    devices: Array.from({ length: 6 }, (_, i) => device(`d${i}`, `machine ${i}`)),
    agents: Array.from({ length: 6 }, (_, i) => ({
      name: `A${i}`,
      activity: "กำลังค้นข้อมูล",
      state: "working" as const,
    })),
    approvals: Array.from({ length: 4 }, (_, i) => approval(`a${i}`)),
    tasks: Array.from({ length: 5 }, (_, i) => task(`t${i}`, `งาน ${i}`)),
  };

  it("stays on the screen however long it runs", () => {
    for (const node of settle(build([], crowd), 2000)) {
      expect(Math.hypot(node.x, node.y)).toBeLessThanOrEqual(BOUNDS + 0.001);
    }
  });

  it("never produces a position that is not a number", () => {
    // Every node stacked exactly on the core: the degenerate case an inverse-square law
    // divides by zero on, and the one a layout is never tested against by looking at it.
    const stacked = build([], crowd).map((node) => ({ ...node, x: 0, y: 0, vx: 0, vy: 0 }));
    for (const node of settle(stacked, 400)) {
      expect(Number.isFinite(node.x)).toBe(true);
      expect(Number.isFinite(node.y)).toBe(true);
    }
  });

  it("survives the machine having been asleep", () => {
    // A huge dt multiplied into an inverse-square force is how a simulation explodes on
    // the first frame after a laptop lid opens.
    let nodes = build([], crowd);
    nodes = step(nodes, 10_000);
    for (const node of nodes) {
      expect(Number.isFinite(node.x)).toBe(true);
      expect(Math.hypot(node.x, node.y)).toBeLessThanOrEqual(BOUNDS + 0.001);
    }
  });

  it("keeps the core where the picture is centred", () => {
    const core = settle(build([], crowd), 500).find((n) => n.kind === "core")!;
    expect(core.x).toBe(0);
    expect(core.y).toBe(0);
  });

  it("settles each kind near its own orbit", () => {
    const settled = settle(build([], crowd), 1200);
    for (const kind of ["work", "waiting", "task", "device"] as const) {
      const distances = settled
        .filter((n) => n.kind === kind)
        .map((n) => Math.hypot(n.x, n.y));
      const mean = distances.reduce((a, b) => a + b, 0) / distances.length;
      expect(Math.abs(mean - ORBIT[kind])).toBeLessThan(ORBIT[kind] * 0.5);
    }
  });

  it("pushes two nodes off each other rather than letting them overlap", () => {
    const two = build([], { ...nothing, devices: [device("a", "one"), device("b", "two")] }).map(
      (node) => (node.kind === "core" ? node : { ...node, x: 100, y: 100 }),
    );
    const settled = settle(two, 600);
    const [a, b] = settled.filter((n) => n.kind === "device");
    expect(Math.hypot(a.x - b.x, a.y - b.y)).toBeGreaterThan(20);
  });
});
