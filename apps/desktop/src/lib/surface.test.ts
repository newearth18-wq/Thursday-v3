/** What a phone may offer, and what it may not (§38, §64, V20). */

import { describe, expect, it } from "vitest";
import {
  NO_STANDING_ON_PHONE,
  PHONE_MAX_WIDTH,
  machineFor,
  mayGrantStanding,
  scopesFor,
  surfaceFor,
} from "@/lib/surface";

describe("which surface this is", () => {
  it("calls a narrow window a phone", () => {
    expect(surfaceFor(390)).toBe("phone");
    expect(surfaceFor(PHONE_MAX_WIDTH)).toBe("phone");
  });

  it("calls anything wider a desktop", () => {
    expect(surfaceFor(PHONE_MAX_WIDTH + 1)).toBe("desktop");
    expect(surfaceFor(1440)).toBe("desktop");
  });
});

describe("a phone may approve but may not grant standing permission", () => {
  it("drops 'always' on a phone even when the engine offered it", () => {
    expect(scopesFor(["once", "always"], "phone")).toEqual(["once"]);
    expect(mayGrantStanding(["once", "always"], "phone")).toBe(false);
  });

  it("keeps it at a desk, where the owner is at the machine", () => {
    expect(scopesFor(["once", "always"], "desktop")).toEqual(["once", "always"]);
    expect(mayGrantStanding(["once", "always"], "desktop")).toBe(true);
  });

  it("never widens what the engine offered", () => {
    // ADR 0008: an ASK_ALWAYS action offers only a one-time answer. A surface cannot invent
    // a scope the engine would refuse to honour.
    expect(mayGrantStanding(["once"], "desktop")).toBe(false);
    expect(mayGrantStanding(["once"], "phone")).toBe(false);
  });

  it("falls back to a one-time answer when the server said nothing", () => {
    expect(scopesFor(undefined, "desktop")).toEqual(["once"]);
    expect(scopesFor([], "phone")).toEqual(["once"]);
  });

  it("has a sentence explaining the absence, so it teaches rather than looks broken", () => {
    expect(NO_STANDING_ON_PHONE).toContain("ครั้งเดียว");
  });
});

describe("naming the machine", () => {
  it("uses the device name when there is one", () => {
    expect(machineFor("Office-PC")).toBe("Office-PC");
  });

  it("says an unknown machine out loud rather than showing a dash", () => {
    // A dash reads as "nothing". The owner is not at the machine, so which machine is part
    // of the question they are being asked.
    for (const nothing of [null, undefined, "", "   "]) {
      expect(machineFor(nothing)).toBe("ไม่ได้ระบุเครื่อง");
    }
  });
});
