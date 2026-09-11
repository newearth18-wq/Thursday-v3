/**
 * The graph Thursday is drawn as — a mind, laid out (Sprint 81).
 *
 * The reference the owner asked for was an Obsidian graph, and the thing that makes one
 * worth looking at is that every dot is a real note. So every node here is a real entity
 * Thursday currently has: a machine that is connected, a job that is running, something
 * waiting on the owner, a task that is open. There are no decorative nodes, no "memory"
 * blob that pulses on a timer, and nothing that appears because the picture looked sparse.
 * When Thursday is idle the graph is one node, and that is the honest picture.
 *
 * Labels come from the same place the rest of the interface's words come from: an agent's
 * job is labelled with the allowlisted phrase from `plain.activity`, never with the class
 * name that arrives in the same payload (Sprint 65).
 *
 * The simulation is a plain function of its inputs so it can be tested rather than watched.
 * A layout that diverges does not look wrong on a good day — it looks fine until the tenth
 * node, and then everything is off-screen or `NaN`.
 */

import { WORKING } from "@/lib/plain";
import type { AgentStatus, Approval, Device, Task } from "@/lib/types";

export type NodeKind = "core" | "device" | "work" | "waiting" | "task";

export interface MindNode {
  id: string;
  kind: NodeKind;
  label: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
}

/**
 * How far from the core each kind settles, in layout units.
 *
 * Kinds sit at different distances so the picture has a readable shape rather than one
 * undifferentiated cloud: what Thursday is doing right now sits closest, what it is
 * connected to sits furthest out.
 */
export const ORBIT: Record<Exclude<NodeKind, "core">, number> = {
  work: 130,
  waiting: 190,
  task: 250,
  device: 320,
};

/** Nothing is allowed past this. A node off-screen is a node that is not information. */
export const BOUNDS = 460;

const REPULSION = 22_000;
const SPRING = 0.012;
const DAMPING = 0.86;
/** The closest two nodes are ever considered, so the inverse-square never divides by zero. */
const FLOOR = 18;

export interface Live {
  devices: Device[];
  agents: AgentStatus[];
  approvals: Approval[];
  tasks: Task[];
}

/**
 * What Thursday is made of at this moment, as nodes.
 *
 * Positions of nodes that were already there are kept: rebuilding from scratch on every
 * poll would make the whole graph jump every five seconds, which reads as noise rather
 * than as change.
 */
export function build(previous: MindNode[], live: Live): MindNode[] {
  const held = new Map(previous.map((node) => [node.id, node]));
  const wanted: Array<Omit<MindNode, "x" | "y" | "vx" | "vy">> = [
    { id: "core", kind: "core", label: "THURSDAY" },
    ...live.agents
      .filter((agent) => agent.state === "working")
      // The phrase, never the name. `AgentStatus.name` exists to tell two concurrent jobs
      // apart and is not something a person is shown.
      .map((agent) => ({
        id: `work:${agent.name}`,
        kind: "work" as const,
        label: agent.activity || WORKING,
      })),
    ...live.approvals.map((approval) => ({
      id: `waiting:${approval.id}`,
      kind: "waiting" as const,
      label: approval.expected_outcome || approval.action,
    })),
    ...live.tasks.map((task) => ({
      id: `task:${task.id}`,
      kind: "task" as const,
      label: task.title,
    })),
    ...live.devices.map((device) => ({
      id: `device:${device.id}`,
      kind: "device" as const,
      label: device.name,
    })),
  ];

  return wanted.map((spec, index) => {
    const existing = held.get(spec.id);
    if (existing) return { ...existing, kind: spec.kind, label: spec.label };
    if (spec.kind === "core") return { ...spec, x: 0, y: 0, vx: 0, vy: 0 };
    // New nodes arrive on their orbit rather than at the centre, so they drift outward
    // into place instead of exploding out of the core.
    const angle = (index * 2.399963) % (Math.PI * 2); // golden angle: no two land together
    const radius = ORBIT[spec.kind];
    return {
      ...spec,
      x: Math.cos(angle) * radius,
      y: Math.sin(angle) * radius,
      vx: 0,
      vy: 0,
    };
  });
}

/**
 * One frame of the layout.
 *
 * Repulsion between every pair, a spring holding each node to its orbit, damping, and a
 * hard clamp. The clamp is not a safety net for a bug — it is the guarantee that however
 * the physics is tuned later, nothing ends up somewhere the owner cannot see it.
 *
 * `dt` is in frames-at-60Hz rather than seconds, so a dropped frame slows the animation
 * instead of launching everything: a large `dt` multiplied into an inverse-square force is
 * how a simulation blows up after the machine sleeps.
 */
export function step(nodes: MindNode[], dt = 1): MindNode[] {
  const bounded = Math.min(Math.max(dt, 0), 3);
  const next = nodes.map((node) => ({ ...node }));

  for (let i = 0; i < next.length; i += 1) {
    const a = next[i];
    if (a.kind === "core") continue; // the core is the frame of reference, and never moves

    let fx = 0;
    let fy = 0;

    for (let j = 0; j < next.length; j += 1) {
      if (i === j) continue;
      const b = next[j];
      const dx = a.x - b.x;
      const dy = a.y - b.y;
      const distance = Math.max(Math.hypot(dx, dy), FLOOR);
      const force = REPULSION / (distance * distance);
      fx += (dx / distance) * force;
      fy += (dy / distance) * force;
    }

    // Held to its orbit rather than to the centre: a spring to the middle would pile every
    // kind on top of the core and the rings would be gone.
    const distance = Math.max(Math.hypot(a.x, a.y), FLOOR);
    const pull = (distance - ORBIT[a.kind]) * SPRING;
    fx -= (a.x / distance) * pull * distance;
    fy -= (a.y / distance) * pull * distance;

    a.vx = (a.vx + fx * 0.0016 * bounded) * DAMPING;
    a.vy = (a.vy + fy * 0.0016 * bounded) * DAMPING;
    a.x += a.vx * bounded;
    a.y += a.vy * bounded;

    const reach = Math.hypot(a.x, a.y);
    if (reach > BOUNDS) {
      a.x = (a.x / reach) * BOUNDS;
      a.y = (a.y / reach) * BOUNDS;
      a.vx = 0;
      a.vy = 0;
    }
  }

  return next;
}
