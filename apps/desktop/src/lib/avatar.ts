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

import type { Mood } from "@/lib/types";

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

/** How close to the edge it turns around. Roughly half its own width. */
export const MARGIN = 46;

/**
 * Which gait each mood walks in.
 *
 * `Record`, so a mood added on the server fails the build here rather than freezing the
 * robot mid-step. Two moods stop it dead and they are the two that should: stopped, because
 * nothing is running, and waiting, because it is asking you something.
 */
export function gaitFor(mood: Mood, intensity: number): Gait {
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
