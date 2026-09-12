/**
 * The canvas, as arithmetic (§48, V15).
 *
 * A node editor is mostly geometry, and geometry that is only ever seen is geometry that
 * is never checked. Dragging a box past the edge, dropping a connection on nothing,
 * reordering two actions so the rule runs backwards — none of those look wrong in a
 * screenshot, and all of them change what the rule does.
 *
 * So everything that decides *where a thing is* or *what order it runs in* lives here as a
 * plain function over plain data, and `WorkflowBuilder.tsx` draws the result. The component
 * owns pointer events and nothing else.
 *
 * The shapes mirror `thursday_automation/workflow.py`. Deliberately: the canvas may not be
 * able to draw anything the engine cannot run, so it does not get its own idea of what a
 * workflow is. Validation here is a *convenience* — the server validates the same graph
 * again on `/preview` and on save, and the server's answer is the one that counts.
 */

export type NodeKind = "trigger" | "condition" | "action";

export interface WorkflowNode {
  id: string;
  kind: NodeKind;
  subkind: string;
  config: Record<string, unknown>;
  x: number;
  y: number;
}

export interface Problem {
  node: string;
  message: string;
  severity: "error" | "warning";
}

export interface Consequence {
  node: string;
  kind: string;
  action: string;
  level: string;
  decision: string;
  blocked: boolean;
  unavailable: string;
}

export interface Graph {
  name: string;
  nodes: WorkflowNode[];
  /** Action ids, in the order the engine will run them. */
  order: string[];
  follow_up_from: number | null;
}

/** Box size in canvas units. Connections attach to the edge, so the width is load-bearing. */
export const NODE_WIDTH = 188;
export const NODE_HEIGHT = 62;

/** The canvas is fixed-size rather than infinite: a node dragged out of sight is a node
 *  the owner has lost, and an infinite plane makes that easy and undoable only by luck. */
export const CANVAS = { width: 900, height: 560 };

const COLUMN: Record<NodeKind, number> = { trigger: 40, condition: 300, action: 600 };

/** Clamp a position to the canvas. Applied on every drag, so nothing ever leaves. */
export function clamp(x: number, y: number): { x: number; y: number } {
  return {
    x: Math.min(Math.max(x, 0), CANVAS.width - NODE_WIDTH),
    y: Math.min(Math.max(y, 0), CANVAS.height - NODE_HEIGHT),
  };
}

/** Where a new node of this kind lands: its column, below whatever is already there. */
export function nextPosition(nodes: WorkflowNode[], kind: NodeKind): { x: number; y: number } {
  const column = nodes.filter((n) => n.kind === kind);
  const lowest = column.reduce((best, n) => Math.max(best, n.y + NODE_HEIGHT + 18), 40);
  return clamp(COLUMN[kind], lowest);
}

/** A stable id. Not random: two nodes created in the same millisecond must still differ, and
 *  a test that cannot predict ids cannot assert on them. */
export function nextId(nodes: WorkflowNode[], kind: NodeKind): string {
  const used = new Set(nodes.map((n) => n.id));
  for (let index = 1; ; index += 1) {
    const candidate = `${kind}-${index}`;
    if (!used.has(candidate)) return candidate;
  }
}

export function addNode(graph: Graph, kind: NodeKind, subkind: string): Graph {
  const id = nextId(graph.nodes, kind);
  const { x, y } = nextPosition(graph.nodes, kind);
  const node: WorkflowNode = { id, kind, subkind, config: {}, x, y };
  return {
    ...graph,
    nodes: [...graph.nodes, node],
    // An action is only in the rule if it is in the order, so adding one appends it.
    order: kind === "action" ? [...graph.order, id] : graph.order,
  };
}

export function removeNode(graph: Graph, id: string): Graph {
  const order = graph.order.filter((n) => n !== id);
  const remaining = graph.nodes.filter((n) => n.id !== id);
  const actions = remaining.filter((n) => n.kind === "action").length;
  return {
    ...graph,
    nodes: remaining,
    order,
    // A split that now points past the end would silently turn a follow-up into a main
    // action, or the reverse. Cleared rather than guessed.
    follow_up_from:
      graph.follow_up_from !== null && graph.follow_up_from <= actions && graph.follow_up_from > 0
        ? graph.follow_up_from
        : null,
  };
}

export function moveNode(graph: Graph, id: string, x: number, y: number): Graph {
  const at = clamp(x, y);
  return { ...graph, nodes: graph.nodes.map((n) => (n.id === id ? { ...n, ...at } : n)) };
}

export function setConfig(graph: Graph, id: string, config: Record<string, unknown>): Graph {
  return { ...graph, nodes: graph.nodes.map((n) => (n.id === id ? { ...n, config } : n)) };
}

export function setSubkind(graph: Graph, id: string, subkind: string): Graph {
  // The config belongs to the old subkind: a `cron` left on an event trigger would be sent
  // to the server and silently ignored, and the owner would never know which one was live.
  return {
    ...graph,
    nodes: graph.nodes.map((n) => (n.id === id ? { ...n, subkind, config: {} } : n)),
  };
}

/**
 * Move an action earlier or later in the run order.
 *
 * The order *is* the rule — actions run in it — so reordering is an edit to behaviour, not
 * to the picture. A drag that reorders by position would make the rule depend on where the
 * owner happened to drop a box; the list is explicit instead.
 */
export function reorder(graph: Graph, id: string, direction: -1 | 1): Graph {
  const from = graph.order.indexOf(id);
  const to = from + direction;
  if (from < 0 || to < 0 || to >= graph.order.length) return graph;
  const order = [...graph.order];
  [order[from], order[to]] = [order[to], order[from]];
  return { ...graph, order };
}

/** Actions in run order. Anything the order forgot still appears, at the end — mirroring
 *  the server, where dropping it would make a box vanish from the rule but not the canvas. */
export function orderedActions(graph: Graph): WorkflowNode[] {
  const actions = graph.nodes.filter((n) => n.kind === "action");
  const byId = new Map(actions.map((n) => [n.id, n]));
  const listed = graph.order.map((id) => byId.get(id)).filter((n): n is WorkflowNode => !!n);
  const seen = new Set(listed.map((n) => n.id));
  return [...listed, ...actions.filter((n) => !seen.has(n.id))];
}

export interface Edge {
  from: string;
  to: string;
}

/**
 * The wires. Derived, never stored.
 *
 * The engine's shape is fixed — one trigger, an AND-set of conditions, then actions in
 * order — so there is nothing for the owner to connect and nothing they could connect
 * wrongly. Letting them draw arrows would mean letting them draw one the engine ignores,
 * which is the failure this whole module exists to avoid (ADR 0064).
 */
export function edges(graph: Graph): Edge[] {
  const trigger = graph.nodes.find((n) => n.kind === "trigger");
  if (!trigger) return [];
  const conditions = graph.nodes.filter((n) => n.kind === "condition");
  const actions = orderedActions(graph);

  const out: Edge[] = conditions.map((c) => ({ from: trigger.id, to: c.id }));
  const heads = conditions.length ? conditions.map((c) => c.id) : [trigger.id];
  if (actions.length) {
    for (const head of heads) out.push({ from: head, to: actions[0].id });
    for (let i = 1; i < actions.length; i += 1) {
      out.push({ from: actions[i - 1].id, to: actions[i].id });
    }
  }
  return out;
}

/** A cubic curve between two boxes, right edge to left edge. */
export function curve(from: WorkflowNode, to: WorkflowNode): string {
  const x1 = from.x + NODE_WIDTH;
  const y1 = from.y + NODE_HEIGHT / 2;
  const x2 = to.x;
  const y2 = to.y + NODE_HEIGHT / 2;
  const reach = Math.max(36, Math.abs(x2 - x1) / 2);
  return `M ${x1} ${y1} C ${x1 + reach} ${y1}, ${x2 - reach} ${y2}, ${x2} ${y2}`;
}

/**
 * Local validation, so the canvas can mark a box before the round trip.
 *
 * Never the last word: `/preview` runs the server's own validator, and anything only it
 * knows — a cron it cannot parse, a tool that does not exist, a handler that is not wired —
 * arrives from there. This function exists to make typing feel responsive, not to decide
 * whether a rule is safe.
 */
export function localProblems(graph: Graph): Problem[] {
  const problems: Problem[] = [];
  const triggers = graph.nodes.filter((n) => n.kind === "trigger");
  if (!graph.name.trim()) problems.push({ node: "", message: "กฎต้องมีชื่อ", severity: "error" });
  if (triggers.length === 0) {
    problems.push({ node: "", message: "ต้องมีตัวกระตุ้น (WHEN) หนึ่งอัน", severity: "error" });
  } else if (triggers.length > 1) {
    problems.push({
      node: "",
      message: `มีตัวกระตุ้น ${triggers.length} อัน — เครื่องมือนี้รับได้อันเดียว`,
      severity: "error",
    });
  }
  if (!graph.nodes.some((n) => n.kind === "action")) {
    problems.push({
      node: "",
      message: "ต้องมีการกระทำ (DO) อย่างน้อยหนึ่งอย่าง",
      severity: "error",
    });
  }
  return problems;
}

export function hasErrors(problems: Problem[]): boolean {
  return problems.some((p) => p.severity === "error");
}

export function problemsFor(problems: Problem[], id: string): Problem[] {
  return problems.filter((p) => p.node === id);
}

export const EMPTY: Graph = { name: "", nodes: [], order: [], follow_up_from: null };
