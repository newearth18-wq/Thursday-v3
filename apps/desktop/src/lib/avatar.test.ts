import { describe, expect, it } from "vitest";
import {
  BLINK_EVERY,
  BLINK_FOR,
  BUBBLE,
  EYES,
  MARGIN,
  POSE,
  RUN_ABOVE,
  SPEED,
  START,
  beat,
  blinking,
  bubbleAt,
  gaitFor,
  stride,
  visorPulse,
  type Gait,
  type Walker,
} from "@/lib/avatar";
import type { Mood, Posture } from "@/lib/types";

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

const POSTURES: Posture[] = [
  "SPEAKING",
  "THINKING",
  "LISTENING",
  "WORKING",
  "SLEEPING",
  "STILL",
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
    for (const mood of MOODS) expect(gaitFor(mood, 0.5, "STILL"), mood).toBeTruthy();
    expect(Object.keys(EYES).sort()).toEqual([...MOODS].sort());
  });

  it("runs when there is a lot going on and walks when there is not", () => {
    expect(gaitFor("WORKING", RUN_ABOVE, "WORKING")).toBe("RUN");
    expect(gaitFor("WORKING", RUN_ABOVE - 0.01, "WORKING")).toBe("WALK");
  });

  it("stops dead for the two states that mean stop", () => {
    // Stopped, because nothing is running. Waiting, because it is asking the owner
    // something, and a robot that wanders off mid-question gets ignored.
    expect(SPEED[gaitFor("STOPPED", 1, "STILL")]).toBe(0);
    expect(SPEED[gaitFor("WAITING", 1, "STILL")]).toBe(0);
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

// ----------------------------------------------------------------------------- postures

describe("what the body does (Sprint 85)", () => {
  it("has a pose for every posture the server can send", () => {
    expect(Object.keys(POSE).sort()).toEqual([...POSTURES].sort());
  });

  it("stops wandering for every posture that faces the owner", () => {
    // §10, §11 and §14 all describe a body that has stopped moving about. A robot that
    // delivers a spoken answer while strolling past is not speaking to anybody.
    for (const posture of ["SPEAKING", "THINKING", "LISTENING"] as Posture[]) {
      for (const mood of MOODS) {
        expect(SPEED[gaitFor(mood, 1, posture)], `${mood}/${posture}`).toBe(0);
      }
    }
  });

  it("sits down to sleep whatever the mood says", () => {
    for (const mood of MOODS) expect(gaitFor(mood, 1, "SLEEPING"), mood).toBe("SIT");
  });

  it("leaves the walking to the mood while working or standing about", () => {
    // The posture is only allowed to say *whether* the body travels. How briskly it travels
    // is still the mood's, which is what keeps one derivation behind both windows.
    for (const mood of MOODS) {
      expect(gaitFor(mood, 0.9, "WORKING")).toBe(gaitFor(mood, 0.9, "STILL"));
    }
    expect(gaitFor("WORKING", 1, "WORKING")).toBe("RUN");
  });

  it("draws no mouth, ever — §14 is a light band and nothing else", () => {
    for (const posture of POSTURES) {
      expect(["steady", "pulse"]).toContain(POSE[posture].visor);
    }
    expect(POSE.SPEAKING.visor).toBe("pulse");
    expect(POSTURES.filter((p) => POSE[p].visor === "pulse")).toEqual(["SPEAKING"]);
  });
});

// -------------------------------------------------------------------------- the clock

describe("the clock that runs when the body does not", () => {
  it("keeps advancing while the robot stands perfectly still", () => {
    // §8: idle must not freeze. `phase` stops with the legs by design, so if the blink and
    // the visor were drawn from it, a standing robot would be a photograph.
    const parked = stride(START, "ALERT", WIDTH, 60);
    expect(parked.phase).toBe(START.phase);
    expect(beat(0, 60)).toBeGreaterThan(0);
  });

  it("blinks regularly, briefly, and not for most of the time", () => {
    let open = 0;
    let shut = 0;
    for (let frame = 0; frame < 6000; frame += 1) {
      if (blinking(frame / 60)) shut += 1;
      else open += 1;
    }
    expect(shut).toBeGreaterThan(0);
    expect(shut / (open + shut)).toBeLessThan(0.1);
    expect(blinking(0)).toBe(true);
    expect(blinking(BLINK_FOR + 0.01)).toBe(false);
    expect(blinking(BLINK_EVERY)).toBe(true);
  });

  it("clamps a long gap so a laptop waking up does not fire a hundred blinks", () => {
    // Same reasoning as `stride`: `dt` is frames-at-60Hz and a suspended machine hands back
    // an enormous one on its first frame.
    expect(beat(0, 100_000)).toBeCloseTo(beat(0, 3), 6);
  });

  it("pulses the visor between nothing and full, without leaving the range", () => {
    for (let frame = 0; frame < 2000; frame += 1) {
      const value = visorPulse(frame / 60);
      expect(value).toBeGreaterThanOrEqual(0);
      expect(value).toBeLessThanOrEqual(1);
    }
    const seen = new Set<number>();
    for (let frame = 0; frame < 200; frame += 1) seen.add(Math.round(visorPulse(frame / 60) * 4));
    expect(seen.size).toBeGreaterThan(2);
  });
});

// ------------------------------------------------------------------ staying on the screen

describe("the bubble stays where it can be read", () => {
  it("never hangs off either edge", () => {
    for (let x = -200; x <= WIDTH + 200; x += 7) {
      const left = bubbleAt(x, WIDTH);
      expect(left - BUBBLE / 2, `left edge at x=${x}`).toBeGreaterThanOrEqual(0);
      expect(left + BUBBLE / 2, `right edge at x=${x}`).toBeLessThanOrEqual(WIDTH);
    }
  });

  it("follows the robot everywhere it is not against an edge", () => {
    expect(bubbleAt(WIDTH / 2, WIDTH)).toBe(WIDTH / 2);
    expect(bubbleAt(BUBBLE, WIDTH)).toBe(BUBBLE);
  });

  it("centres itself on a screen too narrow to hold it", () => {
    // The same answer `stride` gives to a screen narrower than two margins: an impossible
    // constraint gets the least bad position rather than an oscillation between two.
    expect(bubbleAt(10, 100)).toBe(50);
    expect(bubbleAt(90, 100)).toBe(50);
  });

  it("is where the robot turns around, which is why this matters", () => {
    // The bug this fixes was invisible in the tests and obvious in a screenshot: the robot
    // spends much of its time at the margins, because the margins are where it reverses.
    const parked = stride({ x: 0, facing: -1, phase: 0 }, "WALK", WIDTH);
    expect(parked.x).toBe(MARGIN);
    expect(bubbleAt(parked.x, WIDTH)).toBeGreaterThan(parked.x);
  });
});
