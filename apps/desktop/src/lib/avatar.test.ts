import { describe, expect, it } from "vitest";
import {
  EYES,
  MARGIN,
  RUN_ABOVE,
  SPEED,
  START,
  gaitFor,
  stride,
  type Gait,
  type Walker,
} from "@/lib/avatar";
import type { Mood } from "@/lib/types";

const MOODS: Mood[] = [
  "STOPPED",
  "FAILING",
  "CONCERNED",
  "WAITING",
  "UNSURE",
  "WORKING",
  "PLEASED",
  "ATTENTIVE",
  "CALM",
];

const WIDTH = 1200;

const walk = (gait: Gait, frames: number, from: Walker = START, dt = 1): Walker => {
  let walker = from;
  for (let i = 0; i < frames; i += 1) walker = stride(walker, gait, WIDTH, dt);
  return walker;
};

// ------------------------------------------------------------------------------- gaits

describe("which gait a mood walks in", () => {
  it("has one for every mood the server can send", () => {
    for (const mood of MOODS) expect(gaitFor(mood, 0.5), mood).toBeTruthy();
    expect(Object.keys(EYES).sort()).toEqual([...MOODS].sort());
  });

  it("runs when there is a lot going on and walks when there is not", () => {
    expect(gaitFor("WORKING", RUN_ABOVE)).toBe("RUN");
    expect(gaitFor("WORKING", RUN_ABOVE - 0.01)).toBe("WALK");
  });

  it("stops dead for the two states that mean stop", () => {
    // Stopped, because nothing is running. Waiting, because it is asking the owner
    // something, and a robot that wanders off mid-question gets ignored.
    expect(SPEED[gaitFor("STOPPED", 1)]).toBe(0);
    expect(SPEED[gaitFor("WAITING", 1)]).toBe(0);
  });

  it("moves faster the more urgent the gait", () => {
    expect(SPEED.SIT).toBe(0);
    expect(SPEED.ALERT).toBe(0);
    expect(SPEED.IDLE).toBeGreaterThan(0);
    expect(SPEED.WALK).toBeGreaterThan(SPEED.IDLE);
    expect(SPEED.RUN).toBeGreaterThan(SPEED.WALK);
  });
});

// ----------------------------------------------------------------------------- walking

describe("walking", () => {
  it("stays on the screen", () => {
    /**
     * A desktop pet that has drifted off the edge is not obviously broken — it is just
     * gone, and the owner concludes the feature does not work.
     */
    for (const gait of ["IDLE", "WALK", "RUN"] as const) {
      const walker = walk(gait, 5000);
      expect(walker.x, gait).toBeGreaterThanOrEqual(MARGIN - 0.001);
      expect(walker.x, gait).toBeLessThanOrEqual(WIDTH - MARGIN + 0.001);
    }
  });

  it("turns around instead of piling into the edge", () => {
    // Far enough to have crossed the screen once: (width - 2·margin) / speed.
    const across = Math.ceil((WIDTH - 2 * MARGIN) / SPEED.RUN) + 20;
    const back = walk("RUN", across);
    expect(back.facing).toBe(-1);
    expect(back.x).toBeLessThan(WIDTH - MARGIN);

    // And back again, rather than pooling at the left edge.
    const forth = walk("RUN", across * 2 + 20);
    expect(forth.facing).toBe(1);
    expect(forth.x).toBeGreaterThan(MARGIN);
  });

  it("does not move when it is sitting or waiting", () => {
    for (const gait of ["SIT", "ALERT"] as const) {
      expect(walk(gait, 500).x, gait).toBe(START.x);
    }
  });

  it("comes to rest with its feet together", () => {
    const mid = walk("WALK", 37);
    expect(mid.phase).toBeGreaterThan(0);
    expect(walk("ALERT", 60, mid).phase).toBe(0);
  });

  it("survives the machine having been asleep", () => {
    const walker = walk("RUN", 3, START, 100_000);
    expect(Number.isFinite(walker.x)).toBe(true);
    expect(walker.x).toBeLessThanOrEqual(WIDTH - MARGIN + 0.001);
    expect(walker.x).toBeGreaterThanOrEqual(MARGIN - 0.001);
  });

  it("keeps the step cycle inside one turn", () => {
    for (const gait of ["IDLE", "WALK", "RUN"] as const) {
      const walker = walk(gait, 1000);
      expect(walker.phase, gait).toBeGreaterThanOrEqual(0);
      expect(walker.phase, gait).toBeLessThan(1);
    }
  });

  it("stands in the middle of a window too narrow to pace in", () => {
    // A sliver of a screen must not make it oscillate between two impossible edges.
    let walker = START;
    for (let i = 0; i < 200; i += 1) walker = stride(walker, "RUN", 40);
    expect(Number.isFinite(walker.x)).toBe(true);
    expect(walker.x).toBeGreaterThanOrEqual(0);
    expect(walker.x).toBeLessThanOrEqual(40);
  });
});
