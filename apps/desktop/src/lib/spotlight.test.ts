/** Where the arrow goes, and when there is no arrow to draw (§13, V19). */

import { beforeEach, describe, expect, it } from "vitest";
import {
  GAP,
  LABEL_HEIGHT,
  LABEL_WIDTH,
  PADDING,
  type Target,
  arrow,
  locate,
  place,
  selector,
} from "@/lib/spotlight";

const VIEWPORT = { width: 1280, height: 800 };

function target(over: Partial<Target> = {}): Target {
  return { x: 500, y: 400, width: 200, height: 40, ...over };
}

describe("finding the control", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("finds a control the app has marked", () => {
    document.body.innerHTML = `<button data-teach="stop-all">stop all</button>`;
    const found = locate("stop-all", document);
    // jsdom reports zero-sized boxes, so this asserts the lookup rather than the geometry.
    expect(document.querySelector(selector("stop-all"))).not.toBeNull();
    expect(found).toBeNull();
  });

  it("returns nothing for a control that is not on screen", () => {
    // A closed drawer, a button that only appears while something runs, a renamed control.
    // The caller must say so; there is no "draw it roughly there" path.
    expect(locate("stop-all", document)).toBeNull();
  });

  it("treats a zero-sized control as absent", () => {
    // `display:none` and a detached node both measure zero, and an arrow pointing at a
    // zero-sized rectangle points at a corner of the screen.
    document.body.innerHTML = `<button data-teach="ghost" style="display:none">x</button>`;
    expect(locate("ghost", document)).toBeNull();
  });

  it("escapes the name rather than pasting it into a selector", () => {
    expect(selector("stop-all")).toBe('[data-teach="stop-all"]');
    expect(() => document.querySelector(selector('weird"name'))).not.toThrow();
  });
});

describe("placing the ring", () => {
  it("sits outside the control so the highlight is not touching it", () => {
    const { ring } = place(target(), VIEWPORT);
    expect(ring).toEqual({
      x: 500 - PADDING,
      y: 400 - PADDING,
      width: 200 + PADDING * 2,
      height: 40 + PADDING * 2,
    });
  });
});

describe("choosing a side", () => {
  it("prefers above, because the controls worth teaching sit near the bottom", () => {
    expect(place(target(), VIEWPORT).side).toBe("above");
  });

  it("goes below when there is no room above", () => {
    expect(place(target({ y: 4 }), VIEWPORT).side).toBe("below");
  });

  it("goes beside when there is room neither above nor below", () => {
    const squashed = { width: 1280, height: LABEL_HEIGHT + 40 };
    expect(place(target({ y: 10 }), squashed).side).toBe("right");
  });

  it("goes left when the control is against the right edge", () => {
    const squashed = { width: 640, height: LABEL_HEIGHT + 40 };
    const spot = place(target({ x: 600, y: 10, width: 30 }), squashed);
    expect(spot.side).toBe("left");
  });

  it("still draws a label when nothing has room, rather than nothing at all", () => {
    // Half a label off the edge is readable. No label is a silent failure.
    const tiny = { width: 200, height: 80 };
    const spot = place(target({ x: 10, y: 20, width: 180, height: 40 }), tiny);
    expect(spot.label.x).toBeGreaterThanOrEqual(GAP);
    expect(spot.label.y).toBeGreaterThanOrEqual(GAP);
  });
});

describe("keeping the label on screen", () => {
  it("pulls it back from the right edge", () => {
    const spot = place(target({ x: 1240, width: 30 }), VIEWPORT);
    expect(spot.label.x + LABEL_WIDTH).toBeLessThanOrEqual(VIEWPORT.width);
  });

  it("pulls it back from the left edge", () => {
    expect(place(target({ x: 0, width: 20 }), VIEWPORT).label.x).toBeGreaterThanOrEqual(GAP);
  });

  it("pulls it back from the top", () => {
    expect(place(target({ y: 0 }), VIEWPORT).label.y).toBeGreaterThanOrEqual(GAP);
  });
});

describe("the arrow", () => {
  it("runs from the label to the edge of the ring, never across it", () => {
    const spot = place(target(), VIEWPORT);
    const path = arrow(spot);
    // Above: it ends at the ring's top edge, not at its middle.
    expect(path).toContain(`L ${spot.ring.x + spot.ring.width / 2} ${spot.ring.y}`);
  });

  it("leaves from the label's near edge on each side", () => {
    const below = place(target({ y: 4 }), VIEWPORT);
    expect(arrow(below).startsWith(`M ${below.ring.x + below.ring.width / 2} ${below.label.y}`))
      .toBe(true);

    const squashed = { width: 1280, height: LABEL_HEIGHT + 40 };
    const right = place(target({ y: 10 }), squashed);
    expect(arrow(right).startsWith(`M ${right.label.x} `)).toBe(true);
  });
});
