/**
 * Where the arrow goes (§13, V19).
 *
 * §23 carried "the §13 interactive walkthrough that highlights a real button with an arrow"
 * as an open gap, and the reason it is worth building carefully is the same reason it was
 * worth naming: an arrow is a claim about where something *is*. Point it at the wrong place
 * once and the owner stops trusting the next one.
 *
 * So the two things that can be wrong are both decided here, as plain functions over plain
 * numbers rather than as CSS somebody eyeballed:
 *
 * **Whether to draw at all.** A control that is not on screen — a drawer that is closed, a
 * button that only appears while something is running — gets no arrow and a sentence saying
 * so. `locate` returns null and the caller must handle it; there is no "draw it roughly
 * there" path, because roughly there is wrong.
 *
 * **Which side it comes from.** An arrow drawn over the control hides the thing it is
 * pointing at. It goes on whichever side has room, preferring above, and the label goes with
 * it.
 */

/** What a lesson names. The desktop marks its controls with `data-teach="<name>"`. */
export function selector(name: string): string {
  return `[data-teach="${CSS.escape(name)}"]`;
}

export interface Target {
  x: number;
  y: number;
  width: number;
  height: number;
}

export type Side = "above" | "below" | "left" | "right";

export interface Spot {
  /** The ring, inflated a little so the control is not touching its own highlight. */
  ring: Target;
  /** Where the label sits, and which edge of the control the arrow leaves from. */
  side: Side;
  label: { x: number; y: number };
}

/** How far the ring sits outside the control, and how far the label sits from the ring. */
export const PADDING = 6;
export const GAP = 12;
/** Enough room for a line of label. Below this a side has no room and the next is tried. */
export const LABEL_HEIGHT = 56;
export const LABEL_WIDTH = 220;

/**
 * Find a named control on screen.
 *
 * Returns null when it is not there — closed drawer, conditional button, renamed control.
 * A zero-sized box counts as absent: `display:none` and a detached node both measure 0, and
 * an arrow pointing at a zero-sized rectangle is an arrow pointing at a corner of the screen.
 */
export function locate(name: string, root: Document | null = null): Target | null {
  const document_ = root ?? (typeof document === "undefined" ? null : document);
  if (!document_) return null;
  const element = document_.querySelector(selector(name));
  if (!element) return null;
  const box = element.getBoundingClientRect();
  if (box.width <= 0 || box.height <= 0) return null;
  return { x: box.x, y: box.y, width: box.width, height: box.height };
}

/**
 * Place the ring and the label around a control.
 *
 * Above is preferred: a label below a control covers whatever the control is next to in a
 * list, and in this interface the controls worth teaching sit near the bottom of the window.
 */
export function place(
  target: Target,
  viewport: { width: number; height: number },
): Spot {
  const ring: Target = {
    x: target.x - PADDING,
    y: target.y - PADDING,
    width: target.width + PADDING * 2,
    height: target.height + PADDING * 2,
  };

  const room = {
    above: ring.y,
    below: viewport.height - (ring.y + ring.height),
    right: viewport.width - (ring.x + ring.width),
    left: ring.x,
  };

  let side: Side = "above";
  if (room.above < LABEL_HEIGHT + GAP) {
    if (room.below >= LABEL_HEIGHT + GAP) side = "below";
    else if (room.right >= LABEL_WIDTH + GAP) side = "right";
    else if (room.left >= LABEL_WIDTH + GAP) side = "left";
    // Nothing has room: keep "above" and let `clampLabel` pull it back on screen. A label
    // half off the edge is still readable; no label at all is a silent failure.
  }

  const centre = ring.x + ring.width / 2;
  const middle = ring.y + ring.height / 2;
  const raw =
    side === "above"
      ? { x: centre - LABEL_WIDTH / 2, y: ring.y - GAP - LABEL_HEIGHT }
      : side === "below"
        ? { x: centre - LABEL_WIDTH / 2, y: ring.y + ring.height + GAP }
        : side === "right"
          ? { x: ring.x + ring.width + GAP, y: middle - LABEL_HEIGHT / 2 }
          : { x: ring.x - GAP - LABEL_WIDTH, y: middle - LABEL_HEIGHT / 2 };

  return { ring, side, label: clampLabel(raw, viewport) };
}

/** Keep the label on screen. A label the owner cannot read is the same as no label. */
export function clampLabel(
  at: { x: number; y: number },
  viewport: { width: number; height: number },
): { x: number; y: number } {
  return {
    x: Math.min(Math.max(at.x, GAP), Math.max(GAP, viewport.width - LABEL_WIDTH - GAP)),
    y: Math.min(Math.max(at.y, GAP), Math.max(GAP, viewport.height - LABEL_HEIGHT - GAP)),
  };
}

/** The line from the label to the control, as an SVG path. */
export function arrow(spot: Spot): string {
  const { ring, side } = spot;
  const centre = ring.x + ring.width / 2;
  const middle = ring.y + ring.height / 2;
  const from =
    side === "above"
      ? { x: centre, y: spot.label.y + LABEL_HEIGHT }
      : side === "below"
        ? { x: centre, y: spot.label.y }
        : side === "right"
          ? { x: spot.label.x, y: middle }
          : { x: spot.label.x + LABEL_WIDTH, y: middle };
  const to =
    side === "above"
      ? { x: centre, y: ring.y }
      : side === "below"
        ? { x: centre, y: ring.y + ring.height }
        : side === "right"
          ? { x: ring.x + ring.width, y: middle }
          : { x: ring.x, y: middle };
  return `M ${from.x} ${from.y} L ${to.x} ${to.y}`;
}
