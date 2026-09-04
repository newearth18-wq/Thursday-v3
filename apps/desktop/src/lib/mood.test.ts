import { describe, expect, it } from "vitest";
import { APPEARANCE, MOODS, knownMood, withAlpha } from "@/lib/mood";
import type { Mood } from "@/lib/types";

const EXPECTED: Mood[] = [
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
    expect(Object.keys(APPEARANCE).sort()).toEqual([...EXPECTED].sort());
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

describe("a mood this build has never heard of", () => {
  it("does not reach a table lookup that would throw", () => {
    // `APPEARANCE[mood].colour` on an unknown string is a TypeError, and a TypeError in
    // render takes the whole window with it — white screen, no HUD, no avatar, no way to
    // press stop. The `Record` type catches a new mood at build time for *this* build,
    // which is exactly the build that is not the problem: ADR 0057 makes a phone a screen
    // onto a server that may be months newer than the app.
    for (const rubbish of ["EUPHORIC", "", "calm", "undefined", "__proto__", "constructor"]) {
      const mood = knownMood(rubbish);
      expect(MOODS, rubbish).toContain(mood);
      expect(() => APPEARANCE[mood].colour, rubbish).not.toThrow();
    }
    expect(knownMood(null)).toBe("CONCERNED");
    expect(knownMood(undefined)).toBe("CONCERNED");
    expect(knownMood(7)).toBe("CONCERNED");
    expect(knownMood({ mood: "CALM" })).toBe("CONCERNED");
  });

  it("passes every mood it does know through untouched", () => {
    for (const mood of MOODS) expect(knownMood(mood)).toBe(mood);
  });

  it("never falls back to a face that says everything is fine", () => {
    // The two ways of being wrong are not equal. Drawing calm over a failure the client was
    // too old to read is the direction ADR 0054 exists to prevent; drawing concern over a
    // success is merely wrong, and the server's own sentence is still printed underneath.
    expect(knownMood("SOMETHING_NEW")).not.toBe("CALM");
    expect(knownMood("SOMETHING_NEW")).not.toBe("PLEASED");
  });

  it("agrees with the table it guards", () => {
    // MOODS is derived from APPEARANCE, so the two cannot drift; this asserts it is derived
    // and not a second hand-written list.
    expect([...MOODS].sort()).toEqual(Object.keys(APPEARANCE).sort());
  });
});
