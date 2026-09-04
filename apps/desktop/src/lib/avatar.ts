/**
 * How the avatar behaves (Sprint 82).
 *
 * The owner asked for something that moves like a person — that walks about, and runs when
 * there is reason to. So the robot has a gait rather than an animation, and the gait comes
 * from the same derived expression the HUD is drawn from (ADR 0054). It runs because
 * Thursday is busy, sits because Thursday is stopped, and turns to face you because
 * something is waiting on you. Nothing here decides how Thursday feels; it decides how a
 * feeling walks.
 *
 * Everything is a plain function of its inputs. A desktop pet that drifts off the edge of
 * the screen is not obviously broken — it is just gone, and the owner concludes the feature
 * does not work. So the walking is tested rather than watched.
 *
 * No words live here, for the same reason none live in `mood.ts`: the bubble says what the
 * server said.
 */

import type { Mood, Posture } from "@/lib/types";

/** What the body is doing. Five, because a person can tell five apart at thumbnail size. */
export type Gait = "SIT" | "ALERT" | "IDLE" | "WALK" | "RUN";

/** Pixels per frame at 60Hz. */
export const SPEED: Record<Gait, number> = {
  SIT: 0,
  //  Standing and facing you. Stillness is the point: something is waiting on the owner and
  //  a robot that wanders off mid-question is a robot that gets ignored.
  ALERT: 0,
  IDLE: 0.35,
  WALK: 1.1,
  RUN: 2.6,
};

/** Above this much going on, working becomes running. */
export const RUN_ABOVE = 0.75;

/** The drawing's width in pixels — `Robot.tsx`'s default `size`. */
export const ROBOT = 152;

/**
 * How far the widest thing in the drawing reaches from the centre of its 100-unit viewBox.
 *
 * The right ear cup ends at 82 and §10's recording light reaches 87 (`cx=79`, `r=8`), so 37
 * rather than the 32 the body alone would need. The whole drawing mirrors with `facing`, so
 * the wider side has to be assumed on both.
 */
export const REACH = 37;

/**
 * How close to the edge it turns around.
 *
 * Was 46, described as "roughly half its own width", and it was not: half of 152 is 76, and
 * the drawing's own half-width is `REACH / 100 * ROBOT` ≈ 57. The robot turned around with
 * its ear cup and its recording light already off the screen — visible only by looking at
 * a screenshot of the real thing, because a clipped edge is not an error, it is just a
 * slightly narrower robot (Sprint 85). `Robot.test.tsx` now measures the drawing instead of
 * trusting this number.
 */
export const MARGIN = Math.ceil((REACH / 100) * ROBOT);

/**
 * The speech bubble's width in pixels — `max-w-[15rem]` plus its border and padding.
 *
 * Sprint 82 centred the bubble on the robot, which puts half of it off the screen whenever
 * the robot is anywhere near an edge — and the robot spends a good deal of its time at the
 * edges, because that is where it turns around. §9's "do not cover important UI" has a
 * quieter twin: do not put the sentence somewhere it cannot be read.
 */
export const BUBBLE = 248;

/**
 * How far up the bubble sits, in pixels.
 *
 * Derived from the drawing rather than typed as a round number: `Robot.tsx` renders at
 * `size * 1.2` tall inside a container three units off the bottom, so a fixed offset drifts
 * into the robot's face the first time anybody changes its size — which is exactly what the
 * first version of this fix did, and it took a screenshot to notice.
 */
export const BUBBLE_ABOVE = Math.round(ROBOT * 1.2) + 16;

/** Where to draw the bubble so that all of it stays on screen, given where the robot is. */
export function bubbleAt(x: number, width: number): number {
  const half = BUBBLE / 2;
  // A screen narrower than the bubble has no good answer; centring it is the least bad one,
  // and it is the same choice `stride` makes for a screen narrower than two margins.
  if (width <= BUBBLE) return width / 2;
  return Math.min(Math.max(x, half), width - half);
}

/**
 * Which gait each mood walks in.
 *
 * `Record`, so a mood added on the server fails the build here rather than freezing the
 * robot mid-step. Two moods stop it dead and they are the two that should: stopped, because
 * nothing is running, and waiting, because it is asking you something.
 */
export function gaitFor(mood: Mood, intensity: number, posture: Posture): Gait {
  // Posture first, and only for the postures that mean "engaged with the owner". §10, §11
  // and §14 all describe a body that has stopped wandering: turned toward you, leaning in,
  // hand at the chin, visor pulsing. A robot that delivers a spoken answer while strolling
  // past is not speaking to anybody. Everything else — how briskly it moves when it is *not*
  // engaged — is still the mood's to decide, which is why this is two lines and not a
  // second gait table (Sprint 85).
  const pose = POSE[posture];
  if (pose.resting) return "SIT";
  if (!pose.travels) return "ALERT";

  const table: Record<Mood, Gait> = {
    STOPPED: "SIT",
    FAILING: "SIT",
    CONCERNED: "WALK",
    WAITING: "ALERT",
    UNSURE: "WALK",
    WORKING: intensity >= RUN_ABOVE ? "RUN" : "WALK",
    PLEASED: "WALK",
    ATTENTIVE: "ALERT",
    CALM: "IDLE",
  };
  return table[mood];
}

/**
 * What the body does while a posture holds (Sprint 85).
 *
 * Numbers and shapes, never words, for the same reason `mood.ts` holds none: the server
 * owns the sentence. This table is what an animator would be handed — how far the head
 * tilts, where the hand goes, whether the body travels at all.
 */
export interface Pose {
  /** Whether the body walks about. False for every posture that faces the owner. */
  travels: boolean;
  /** Head tilt in degrees. §11's "head tilt, look up" while thinking. */
  tilt: number;
  /** Lean toward the owner in degrees. §10's "lean forward" while listening. */
  lean: number;
  /** §11 puts a hand near the chin; §18 raises one toward the approval it is waiting on. */
  hand: "none" | "chin" | "raised";
  /** §14. Thursday has no mouth, so speech is a band of light across the visor. */
  visor: "steady" | "pulse";
  /** §20. Sat down, eyes dimmed, waiting for a wake word. */
  resting: boolean;
}

export const POSE: Record<Posture, Pose> = {
  SPEAKING: { travels: false, tilt: 0, lean: 0, hand: "none", visor: "pulse", resting: false },
  THINKING: { travels: false, tilt: -9, lean: 0, hand: "chin", visor: "steady", resting: false },
  LISTENING: { travels: false, tilt: 3, lean: 6, hand: "none", visor: "steady", resting: false },
  WORKING: { travels: true, tilt: 0, lean: 0, hand: "none", visor: "steady", resting: false },
  SLEEPING: { travels: false, tilt: 14, lean: 0, hand: "none", visor: "steady", resting: true },
  STILL: { travels: true, tilt: 0, lean: 0, hand: "none", visor: "steady", resting: false },
};

/**
 * The colour of the recording indicator (§10).
 *
 * Deliberately *not* in `mood.ts`. Every colour in that file is a mood's, and a mood that
 * could choose this one could choose to make it invisible — which is the same failure the
 * server side avoids by keeping `listening` outside both priority tables. A recording light
 * answers to nothing.
 */
export const MIC = "#ef4444";

/**
 * A clock that keeps running when the body does not, in seconds.
 *
 * `phase` deliberately stops when the robot stops, so the legs do not slide. But §8 requires
 * a still robot to keep blinking and §14 requires a standing one to pulse its visor while it
 * speaks — both of which would freeze if they were drawn from `phase`. Clamped like
 * `stride`, so a laptop waking from sleep does not fire two hundred blinks at once.
 */
export function beat(previous: number, dt = 1): number {
  return (previous + Math.min(Math.max(dt, 0), 3) / 60) % 3600;
}

/** Seconds between blinks, and how long one lasts. §8: idle must never be frozen. */
export const BLINK_EVERY = 4.2;
export const BLINK_FOR = 0.13;

/**
 * Whether the eyes are shut this instant.
 *
 * The blink sits at the *end* of each cycle, not the start, and that is a correctness
 * requirement rather than a preference. `Avatar.tsx` stops advancing the clock entirely
 * when the owner has asked for reduced motion, so zero is not merely the first frame — it
 * is the resting value the robot is drawn at forever on those machines. The first version
 * blinked at `clock % BLINK_EVERY < BLINK_FOR`, which is true at zero, so
 * `prefers-reduced-motion` did not calm the robot down: it blinded it, permanently, and
 * flashed a shut-eyed robot on every mount besides.
 *
 * **A frozen animation clock has to render the pose at rest.** `Robot.test.tsx` asserts it
 * for this and for the visor, so the next thing driven from `clock` has to answer for its
 * zero too.
 */
export function blinking(clock: number): boolean {
  return clock % BLINK_EVERY >= BLINK_EVERY - BLINK_FOR;
}

/** Every posture, as data — so a runtime value can be checked against what can be drawn. */
export const POSTURES = Object.keys(POSE) as Posture[];

/**
 * A posture this client can actually draw, or the quietest one.
 *
 * The server is not always this build. ADR 0057 makes a phone a screen onto a Thursday
 * running somewhere else, which is precisely the arrangement where the two are different
 * versions — and `POSE[posture]` on an unrecognised string is `undefined`, whose `.resting`
 * throws and takes the whole window with it. So the tables are the allowlist, checked once
 * here, rather than nine call sites each hoping.
 */
export function knownPosture(value: unknown): Posture {
  // `Object.hasOwn` rather than `in`, for the reason spelled out on `knownMood`: `in`
  // walks the prototype chain, so `"__proto__"` and `"constructor"` both pass it.
  return typeof value === "string" && Object.hasOwn(POSE, value) ? (value as Posture) : "STILL";
}

/** 0–1, the visor's light band while speaking. §14 — a pulse, never a mouth. */
export function visorPulse(clock: number): number {
  return (Math.sin(clock * 9) + 1) / 2;
}

/** What the face does. Shapes, not words — `Robot.tsx` draws them. */
export type Eyes = "OPEN" | "HAPPY" | "WORRIED" | "SHUT" | "WIDE";

export const EYES: Record<Mood, Eyes> = {
  STOPPED: "SHUT",
  FAILING: "WORRIED",
  CONCERNED: "WORRIED",
  WAITING: "WIDE",
  UNSURE: "WORRIED",
  WORKING: "OPEN",
  PLEASED: "HAPPY",
  ATTENTIVE: "WIDE",
  CALM: "OPEN",
};

export interface Walker {
  /** Distance from the left edge, in pixels. */
  x: number;
  /** 1 facing right, -1 facing left. */
  facing: 1 | -1;
  /** Where it is in its step cycle, 0–1. Drives the legs, the arms and the bob. */
  phase: number;
}

export const START: Walker = { x: MARGIN, facing: 1, phase: 0 };

/**
 * One frame of walking.
 *
 * `dt` is frames-at-60Hz and clamped, so a machine that was asleep for an hour does not
 * teleport the robot across three monitors on its first frame back.
 */
export function stride(walker: Walker, gait: Gait, width: number, dt = 1): Walker {
  const bounded = Math.min(Math.max(dt, 0), 3);
  const speed = SPEED[gait];
  // A screen narrower than two margins would give an empty range; the robot stands in the
  // middle of it rather than oscillating between two impossible edges.
  const left = Math.min(MARGIN, width / 2);
  const right = Math.max(width - MARGIN, width / 2);

  let facing = walker.facing;
  let x = walker.x + speed * facing * bounded;

  if (x <= left) {
    x = left;
    facing = 1;
  } else if (x >= right) {
    x = right;
    facing = -1;
  }

  // The step cycle runs at the pace of the walking, so the legs match the ground rather
  // than sliding along it.
  let phase: number;
  if (speed > 0) {
    phase = (walker.phase + speed * 0.055 * bounded) % 1;
  } else {
    // Coming to a stop, the cycle finishes its step rather than freezing with one leg in
    // the air — phase 0 is the pose with the feet together.
    const step = 0.06 * bounded;
    phase = walker.phase === 0 || walker.phase + step >= 1 ? 0 : walker.phase + step;
  }

  return { x, facing, phase };
}
