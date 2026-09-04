/**
 * How each mood is drawn. Colour and motion — never words.
 *
 * The split is deliberate and it is the whole point of this file. Sprint 65 put every
 * user-facing phrase behind an allowlist on the server, and Sprint 80 made the sentence part
 * of the expression itself. So the client is given `because` already written, and has no
 * business composing its own: a phrase table here would be a second allowlist to forget to
 * update, which is exactly how the leak gets back in.
 *
 * **The server owns the words. This file owns the pixels.** `mood.test.ts` asserts it, by
 * checking that nothing in here is text a person could read.
 */

import type { Mood } from "@/lib/types";

export interface Appearance {
  /** The core colour, as a CSS value — the canvas needs a string, not a Tailwind class. */
  colour: string;
  /** Seconds for one full turn of the outer ring. Lower is more agitated. */
  spin: number;
  /** How far the core breathes, as a fraction of its radius. */
  breath: number;
}

/**
 * Every mood, exhaustively — `Record` rather than a partial map, so adding one to the
 * server's enum fails the build here instead of rendering as undefined at three in the
 * morning.
 *
 * The palette is the one Thursday already uses for state, extended by two. It runs cool
 * when things are fine and warm when they are not, which is the only distinction the owner
 * should have to make from across the room.
 */
export const APPEARANCE: Record<Mood, Appearance> = {
  // Everything stopped. Grey and nearly still: not an alarm, an absence.
  STOPPED: { colour: "#64748b", spin: 90, breath: 0.01 },
  FAILING: { colour: "#f87171", spin: 9, breath: 0.09 },
  CONCERNED: { colour: "#fb923c", spin: 16, breath: 0.06 },
  WAITING: { colour: "#fbbf24", spin: 12, breath: 0.08 },
  UNSURE: { colour: "#a78bfa", spin: 22, breath: 0.05 },
  WORKING: { colour: "#38bdf8", spin: 7, breath: 0.07 },
  PLEASED: { colour: "#34d399", spin: 26, breath: 0.05 },
  ATTENTIVE: { colour: "#7dd3fc", spin: 14, breath: 0.06 },
  CALM: { colour: "#6ea8fe", spin: 40, breath: 0.035 },
};

/** `#rrggbb` plus an alpha, for the glows. Kept here so no component hand-rolls it. */
export function withAlpha(colour: string, alpha: number): string {
  const value = Math.round(Math.min(1, Math.max(0, alpha)) * 255)
    .toString(16)
    .padStart(2, "0");
  return `${colour}${value}`;
}
