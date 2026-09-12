/** The canvas's arithmetic — the parts that change what a rule does (V15). */

import { describe, expect, it } from "vitest";
import {
  CANVAS,
  EMPTY,
  type Graph,
  NODE_HEIGHT,
  NODE_WIDTH,
  addNode,
  clamp,
  curve,
  edges,
  hasErrors,
  localProblems,
  moveNode,
  nextId,
  orderedActions,
  removeNode,
  reorder,
  setConfig,
  setSubkind,
} from "@/lib/workflow";

function graph(): Graph {
  let g = addNode({ ...EMPTY, name: "สรุปเช้า" }, "trigger", "schedule");
  g = addNode(g, "action", "notify");
  g = addNode(g, "action", "task");
  return g;
}

describe("staying on the canvas", () => {
  it("clamps a drag to the visible area", () => {
    expect(clamp(-40, -40)).toEqual({ x: 0, y: 0 });
    expect(clamp(9999, 9999)).toEqual({
      x: CANVAS.width - NODE_WIDTH,
      y: CANVAS.height - NODE_HEIGHT,
    });
  });

  it("a node dragged off the edge is a node the owner has lost, so it cannot be", () => {
    const g = moveNode(graph(), "trigger-1", -500, 5000);
    const node = g.nodes.find((n) => n.id === "trigger-1")!;
    expect(node.x).toBe(0);
    expect(node.y).toBe(CANVAS.height - NODE_HEIGHT);
  });

  it("stacks new nodes of a kind rather than piling them on one spot", () => {
    const g = graph();
    const [first, second] = g.nodes.filter((n) => n.kind === "action");
    expect(second.y).toBeGreaterThan(first.y);
    expect(second.x).toBe(first.x);
  });
});

describe("ids", () => {
  it("are predictable, so two nodes made in the same millisecond still differ", () => {
    const g = graph();
    expect(g.nodes.map((n) => n.id)).toEqual(["trigger-1", "action-1", "action-2"]);
  });

  it("skips an id that is still in use", () => {
    const g = removeNode(graph(), "action-1");
    expect(nextId(g.nodes, "action")).toBe("action-1");
    expect(nextId(addNode(g, "action", "notify").nodes, "action")).toBe("action-3");
  });
});

describe("the run order is the rule", () => {
  it("a new action goes on the end of the order, not just onto the canvas", () => {
    expect(graph().order).toEqual(["action-1", "action-2"]);
  });

  it("reordering swaps two neighbours", () => {
    expect(reorder(graph(), "action-2", -1).order).toEqual(["action-2", "action-1"]);
  });

  it("reordering past either end does nothing rather than wrapping", () => {
    const g = graph();
    expect(reorder(g, "action-1", -1)).toBe(g);
    expect(reorder(g, "action-2", 1)).toBe(g);
  });

  it("an action missing from the order still reaches the rule", () => {
    // Dropping it would make a box vanish from the rule while staying on the canvas.
    const g = { ...graph(), order: ["action-1"] };
    expect(orderedActions(g).map((n) => n.id)).toEqual(["action-1", "action-2"]);
  });

  it("removing a node removes it from the order too", () => {
    expect(removeNode(graph(), "action-1").order).toEqual(["action-2"]);
  });

  it("a follow-up split left pointing past the end is cleared, not guessed", () => {
    // Keeping it would silently turn a follow-up into a main action, or the reverse.
    const g = { ...graph(), follow_up_from: 1 };
    expect(removeNode(g, "action-1").follow_up_from).toBe(1);
    expect(removeNode(removeNode(g, "action-1"), "action-2").follow_up_from).toBe(null);
  });
});

describe("editing a node", () => {
  it("keeps the config the owner typed", () => {
    const g = setConfig(graph(), "trigger-1", { cron: "30 7 * * 1-5" });
    expect(g.nodes.find((n) => n.id === "trigger-1")!.config).toEqual({ cron: "30 7 * * 1-5" });
  });

  it("clears the config when the kind changes", () => {
    // A `cron` left on an event trigger is sent to the server and silently ignored, and
    // the owner never learns which field was live.
    const g = setConfig(graph(), "trigger-1", { cron: "30 7 * * 1-5" });
    expect(setSubkind(g, "trigger-1", "event").nodes[0].config).toEqual({});
  });

  it("leaves every other node alone", () => {
    const before = graph();
    const after = setConfig(before, "action-1", { title: "x" });
    expect(after.nodes.find((n) => n.id === "action-2")).toBe(
      before.nodes.find((n) => n.id === "action-2"),
    );
  });
});

describe("the wires are derived, never drawn", () => {
  it("runs trigger → actions when there are no conditions", () => {
    expect(edges(graph())).toEqual([
      { from: "trigger-1", to: "action-1" },
      { from: "action-1", to: "action-2" },
    ]);
  });

  it("puts every condition between the trigger and the first action", () => {
    // The engine evaluates `all(...)`, so conditions are an AND-set with no ordering. A
    // canvas that let the owner wire them in series would be drawing a promise.
    const g = addNode(graph(), "condition", "eq");
    expect(edges(g)).toEqual([
      { from: "trigger-1", to: "condition-1" },
      { from: "condition-1", to: "action-1" },
      { from: "action-1", to: "action-2" },
    ]);
  });

  it("follows the run order, not the positions", () => {
    const g = reorder(graph(), "action-2", -1);
    expect(edges(g)).toContainEqual({ from: "action-2", to: "action-1" });
  });

  it("draws nothing without a trigger", () => {
    expect(edges({ ...EMPTY, nodes: graph().nodes.filter((n) => n.kind === "action") })).toEqual([]);
  });

  it("curves from one box's right edge to the next box's left edge", () => {
    const [a, b] = [
      { id: "a", kind: "action" as const, subkind: "notify", config: {}, x: 0, y: 0 },
      { id: "b", kind: "action" as const, subkind: "notify", config: {}, x: 300, y: 100 },
    ];
    expect(curve(a, b)).toBe(
      `M ${NODE_WIDTH} ${NODE_HEIGHT / 2} C 244 31, 244 131, 300 131`,
    );
  });
});

describe("local validation is a convenience, not the verdict", () => {
  it("says nothing about a complete graph", () => {
    expect(localProblems({ ...graph(), name: "สรุปเช้า" })).toEqual([]);
  });

  it("catches the three things the canvas can see for itself", () => {
    expect(hasErrors(localProblems({ ...graph(), name: " " }))).toBe(true);
    expect(hasErrors(localProblems({ ...EMPTY, name: "x" }))).toBe(true);
    const twoTriggers = addNode(graph(), "trigger", "manual");
    expect(localProblems(twoTriggers).some((p) => p.message.includes("อันเดียว"))).toBe(true);
  });

  it("does not try to judge a cron — the server owns that answer", () => {
    const g = setConfig(graph(), "trigger-1", { cron: "ทุกเช้า" });
    expect(localProblems(g)).toEqual([]);
  });
});
