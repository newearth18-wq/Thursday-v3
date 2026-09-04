import { describe, expect, it } from "vitest";
import { APPEARANCE, withAlpha } from "@/lib/mood";
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

describe("how a mood is drawn", () => {
  it("has an appearance for every mood the server can send", () => {
    expect(Object.keys(APPEARANCE).sort()).toEqual([...MOODS].sort());
  });

  it("gives every mood a real colour and a finite motion", () => {
    for (const [mood, look] of Object.entries(APPEARANCE)) {
      expect(look.colour, mood).toMatch(/^#[0-9a-f]{6}$/i);
      expect(look.spin, mood).toBeGreaterThan(0);
      expect(look.breath, mood).toBeGreaterThanOrEqual(0);
      expect(look.breath, mood).toBeLessThan(0.5);
    }
  });

  it("has nowhere to put a word", () => {
    /**
     * The server owns the words. A phrase table here would be a second allowlist to forget
     * to update, and Sprint 65's argument is that the leak arrives through the copy
     * somebody did not know existed. So the guard is on the shape: three keys, and the only
     * string among them is a colour. A `label` added later fails here rather than shipping.
     */
    for (const [mood, look] of Object.entries(APPEARANCE)) {
      expect(Object.keys(look).sort(), mood).toEqual(["breath", "colour", "spin"]);
      const strings = Object.values(look).filter((value) => typeof value === "string");
      expect(strings, mood).toEqual([look.colour]);
    }
  });
});

describe("withAlpha", () => {
  it("appends the channel the canvas expects", () => {
    expect(withAlpha("#6ea8fe", 1)).toBe("#6ea8feff");
    expect(withAlpha("#6ea8fe", 0)).toBe("#6ea8fe00");
    expect(withAlpha("#6ea8fe", 0.5)).toMatch(/^#6ea8fe[0-9a-f]{2}$/);
  });

  it("clamps rather than producing an unparseable colour", () => {
    expect(withAlpha("#6ea8fe", 4)).toBe("#6ea8feff");
    expect(withAlpha("#6ea8fe", -2)).toBe("#6ea8fe00");
  });
});
