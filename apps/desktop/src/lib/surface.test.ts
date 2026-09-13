/** What a phone may offer, and what it may not (§38, §64, V20). */

import { describe, expect, it } from "vitest";
import {
  NO_STANDING_ON_PHONE,
  PHONE_MAX_WIDTH,
  approvalRefusalFor,
  machineFor,
  mayActFromPhone,
  mayApproveFromPhone,
  mayGrantStanding,
  refusalFor,
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

describe("what a phone may do to a machine", () => {
  it("may lock and wake — both recoverable", () => {
    expect(mayActFromPhone("system.lock", "phone")).toBe(true);
    expect(mayActFromPhone("device.wake", "phone")).toBe(true);
  });

  it("may not shut a machine down or restart it", () => {
    // Not because it is high-risk in the abstract: because the owner is in a different
    // building and cannot take it back. The apparent undo is wake-on-LAN, and §23 says
    // plainly that waking a machine has never woken a machine.
    expect(mayActFromPhone("system.power", "phone")).toBe(false);
  });

  it("may not run a shell or delete a file either", () => {
    expect(mayActFromPhone("shell.run", "phone")).toBe(false);
    expect(mayActFromPhone("powershell.run", "phone")).toBe(false);
    expect(mayActFromPhone("file.delete", "phone")).toBe(false);
  });

  it("is an allowlist, so a verb added to the catalogue is off the phone by default", () => {
    // The safe direction for a list somebody will forget to update.
    expect(mayActFromPhone("some.future.verb", "phone")).toBe(false);
  });

  it("does not narrow the desktop, where the owner is at the machine", () => {
    expect(mayActFromPhone("system.power", "desktop")).toBe(true);
    expect(refusalFor("system.power", "desktop")).toBe("");
  });

  it("explains each refusal rather than leaving a control silently absent", () => {
    expect(refusalFor("system.power", "phone")).toContain("ยังไม่ได้บันทึกจะหาย");
    expect(refusalFor("shell.run", "phone")).not.toBe("");
  });
});

describe("a phone may not authorise what it may not initiate (V27)", () => {
  it("refuses the approval for exactly what the button refuses", () => {
    // One list, read twice. ADR 0070 guarded the button and left the approval beside it open,
    // and `system.power` being ASK_ALWAYS for every caller made that approval the normal path.
    expect(mayActFromPhone("system.power", "phone")).toBe(false);
    expect(mayApproveFromPhone("system.power", "phone")).toBe(false);
  });

  it("still lets a phone answer everything else, which is why it exists", () => {
    // §64: see what is happening, answer what is being asked. An allowlist here would make
    // every unfamiliar action unanswerable and teach the owner to walk to the desk for all
    // of them — which does not make them safer.
    for (const action of [
      "file.delete",
      "email.send",
      "shell.run",
      "some.future.verb",
    ]) {
      expect(mayApproveFromPhone(action, "phone")).toBe(true);
    }
  });

  it("is a denylist, and that asymmetry with the button is deliberate", () => {
    // Initiating fails closed for free — the owner uses the desktop. Answering fails closed
    // at the cost of the phone's whole purpose, so the two lists point opposite ways.
    expect(mayActFromPhone("some.future.verb", "phone")).toBe(false);
    expect(mayApproveFromPhone("some.future.verb", "phone")).toBe(true);
  });

  it("does not narrow the desktop, where the owner is at the machine", () => {
    expect(mayApproveFromPhone("system.power", "desktop")).toBe(true);
    expect(approvalRefusalFor("system.power", "desktop")).toBe("");
  });

  it("says where the answer belongs rather than only that it is refused", () => {
    const reason = approvalRefusalFor("system.power", "phone");
    expect(reason).toContain("ยังไม่ได้บันทึกจะหาย");
    expect(reason).toContain("หน้าเครื่อง");
  });
});
