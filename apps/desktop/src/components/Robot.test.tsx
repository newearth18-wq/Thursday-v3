import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Robot } from "@/components/Robot";
import {
  BLINK_EVERY,
  MARGIN,
  MIC,
  POSE,
  PROPS,
  REACH,
  ROBOT,
  blinking,
  gaitFor,
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

function draw(over: Partial<Parameters<typeof Robot>[0]> = {}) {
  const mood: Mood = over.mood ?? "CALM";
  const posture: Posture = over.posture ?? "STILL";
  return render(
    <Robot
      mood={mood}
      posture={posture}
      listening={false}
      gait={gaitFor(mood, 0.5, posture)}
      phase={0}
      clock={0}
      facing={1}
      {...over}
    />,
  );
}

// ------------------------------------------------------------------ §10, the microphone

describe("the recording indicator", () => {
  it("is drawn in every mood and every posture there is", () => {
    // The whole point of §10 living outside both priority tables. The failure mode is not
    // "the light is wrong" — it is "the light is right until something more urgent happens",
    // and the moment it most matters is the moment something urgent is happening. So this
    // walks all fifty-four combinations rather than a representative few.
    for (const mood of MOODS) {
      for (const posture of POSTURES) {
        const { container, unmount } = draw({ mood, posture, listening: true });
        const light = container.querySelector('[data-listening="true"]');
        expect(light, `${mood} / ${posture} hid the microphone`).not.toBeNull();
        expect(light?.querySelectorAll("circle").length).toBeGreaterThan(0);
        unmount();
      }
    }
  });

  it("is not drawn when the microphone is closed", () => {
    // Otherwise the test above would pass against a light that is simply always on, which
    // is a different lie about the same thing.
    const { container } = draw({ listening: false });
    expect(container.querySelector('[data-listening="true"]')).toBeNull();
    expect(container.querySelector('[data-listening="false"]')?.children.length).toBe(0);
  });

  it("is not drawn in any colour a mood could choose", () => {
    // `APPEARANCE` is the mood palette; a recording light drawn from it is one a mood can
    // change, and a mood that can change it is a mood that can hide it.
    const { container } = draw({ mood: "FAILING", listening: true });
    const light = container.querySelector('[data-listening="true"] circle');
    expect(light?.getAttribute("fill")).toBe(MIC);
  });

  it("survives a blink, which closes the eyes and nothing else", () => {
    const { container } = draw({ listening: true, clock: BLINK_EVERY });
    expect(container.querySelector('[data-listening="true"]')).not.toBeNull();
  });
});

// ------------------------------------------------------------------------- §14, no mouth

describe("the face", () => {
  it("shows speech on the visor and nowhere else — §14, structurally", () => {
    // Written twice. The first version scanned the markup for /mouth|lip|teeth/ and failed
    // against a drawing that has no mouth at all, because `ellipse` contains "lip": a text
    // scan matching a word inside an unrelated token, which is the same defect this project
    // has fixed in three other places. What §14 actually asks is answerable structurally —
    // when Thursday speaks, exactly one element in the drawing changes, and it sits on the
    // visor rather than below the eyes where a mouth would be.
    // Each element's *own* attributes, not its `outerHTML` — the first version used the
    // latter and counted four changes for one moving rect, because a parent's markup
    // contains its children's. An ancestor is not a second thing that moved.
    const at = (clock: number) =>
      [...draw({ posture: "SPEAKING", clock }).container.querySelectorAll("*")].map((node) =>
        [node.tagName, ...[...node.attributes].map((a) => `${a.name}=${a.value}`)].join("|"),
      );

    // Both clocks chosen outside a blink. The first attempt used 0 and 0.17, and the
    // element counts differed by one — because a blink swaps two glowing eyes for two
    // closed lids, not because anything on the face moved. §8 and §14 are separate claims
    // and a test for one must not be answered by the other.
    const quiet = at(0.5);
    const loud = at(0.72);
    expect(blinking(0.5) || blinking(0.72)).toBe(false);
    expect(quiet.length).toBe(loud.length);

    const moved = quiet.filter((html, index) => html !== loud[index]);
    expect(moved.length, "more than one thing moved when Thursday spoke").toBe(1);

    expect(moved[0].startsWith("rect|")).toBe(true);
    // The visor is y=35..59 and the eyes sit at y≈52, so the one thing that moves is on the
    // face plate. A mouth would be below both.
    expect(moved[0]).toContain("y=56");
  });

  it("draws nothing at all on the face when Thursday is not speaking", () => {
    for (const posture of POSTURES.filter((p) => p !== "SPEAKING")) {
      const { container, unmount } = draw({ posture, clock: 0.17 });
      expect(container.querySelector('rect[y="56"]'), posture).toBeNull();
      unmount();
    }
  });

  it("lights a band across the visor while speaking, and only then", () => {
    const speaking = draw({ posture: "SPEAKING", clock: 0.17 }).container;
    const thinking = draw({ posture: "THINKING", clock: 0.17 }).container;
    const bands = (root: Element) =>
      [...root.querySelectorAll("rect")].filter((r) => r.getAttribute("y") === "56").length;

    expect(bands(speaking)).toBe(1);
    expect(bands(thinking)).toBe(0);
  });

  it("moves the band over time, because the body is standing still to speak", () => {
    // §14's pulse is the only motion during an utterance: `gaitFor` stops the legs for
    // SPEAKING, so `phase` is frozen and a band drawn from it would be a still image.
    const width = (clock: number) =>
      draw({ posture: "SPEAKING", clock })
        .container.querySelector('rect[y="56"]')
        ?.getAttribute("width");

    expect(width(0.5)).not.toBe(width(0.72));
  });
});

// --------------------------------------------------------------------------- §8, §11, §20

describe("the body language the addendum asks for", () => {
  it("tilts the head to think and leans the body to listen", () => {
    const heads = (posture: Posture) =>
      draw({ posture }).container.innerHTML.includes(`rotate(${POSE[posture].tilt} 50 46)`);

    expect(heads("THINKING")).toBe(true);
    expect(POSE.THINKING.tilt).not.toBe(0);
    expect(POSE.LISTENING.lean).toBeGreaterThan(0);
    expect(draw({ posture: "LISTENING" }).container.innerHTML).toContain(
      `rotate(${POSE.LISTENING.lean} 50 100)`,
    );
  });

  it("brings a hand to the chin to think, and a different one up to ask", () => {
    const arm = (over: Partial<Parameters<typeof Robot>[0]>) => {
      const html = draw(over).container.innerHTML;
      return html.match(/rotate\((-?[\d.]+) 70 72\)/)?.[1];
    };

    const chin = arm({ posture: "THINKING" });
    const asking = arm({ mood: "WAITING", posture: "STILL" });
    expect(chin).toBeTruthy();
    expect(asking).toBeTruthy();
    expect(chin).not.toBe(asking);
  });

  it("never freezes when idle — a blink happens with nothing else moving", () => {
    // §8. `phase` is zero throughout: if anything here moved because of the step cycle, the
    // robot would be still and this would pass for the wrong reason.
    const open = draw({ posture: "STILL", clock: 1.0 }).container.innerHTML;
    const shut = draw({ posture: "STILL", clock: BLINK_EVERY - 0.05 }).container.innerHTML;
    expect(open).not.toBe(shut);
  });

  it("is drawn awake when the clock is frozen for reduced motion", () => {
    // `Avatar.tsx` never advances `clock` when the owner has asked for less motion, so the
    // robot they see is the one at `clock: 0` — for the entire session. It used to be drawn
    // with its eyes shut, which is not less motion, it is a different robot: asking for a
    // calmer animation blinded it permanently and there was no way back.
    const resting = draw({ posture: "STILL", clock: 0 }).container;
    // SHUT is two straight strokes; every other eye shape draws ellipses.
    expect(resting.innerHTML).not.toContain('d="M34 52 h9"');
    expect(resting.querySelectorAll("ellipse").length).toBeGreaterThan(1);
  });

  it("does not blink while asleep", () => {
    // §20: eyes dimmed and waiting for a wake word. A sleeping robot that keeps blinking is
    // not asleep, it is staring.
    const a = draw({ posture: "SLEEPING", clock: 1.0 }).container.innerHTML;
    const b = draw({ posture: "SLEEPING", clock: BLINK_EVERY }).container.innerHTML;
    expect(a).toBe(b);
  });
});

// ------------------------------------------------------------ staying on the screen

describe("how wide the drawing actually is", () => {
  it("fits inside the margin it turns around at", () => {
    // `MARGIN` used to be 46 and its comment claimed "roughly half its own width" — it was
    // not, and the robot turned around with an ear and its recording light already past the
    // edge of the screen. Nothing failed: a clipped robot is not an error, it is a slightly
    // narrower robot, which is why this measures the drawing rather than restating a number.
    const { container } = draw({ listening: true });
    const reaches: number[] = [];

    for (const node of container.querySelectorAll("rect, circle, ellipse")) {
      const n = (name: string) => Number(node.getAttribute(name) ?? 0);
      const [left, right] =
        node.tagName === "rect"
          ? [n("x"), n("x") + n("width")]
          : node.tagName === "circle"
            ? [n("cx") - n("r"), n("cx") + n("r")]
            : [n("cx") - n("rx"), n("cx") + n("rx")];
      reaches.push(Math.abs(left - 50), Math.abs(right - 50));
    }

    const widest = Math.max(...reaches);
    expect(widest).toBeLessThanOrEqual(REACH);
    // And REACH is not padded into meaninglessness: something really does reach that far.
    expect(widest).toBeGreaterThan(REACH - 4);
    expect(MARGIN).toBeGreaterThanOrEqual((widest / 100) * ROBOT);
  });
});

// ------------------------------------------------------------------ §13, what it holds

describe("the prop", () => {
  it("draws exactly one object, and only when there is something to hold", () => {
    const empty = draw({ prop: "NONE", posture: "WORKING" }).container;
    expect(empty.querySelector("[data-prop]")).toBeNull();

    for (const prop of PROPS.filter((p) => p !== "NONE")) {
      const { container, unmount } = draw({ prop, posture: "WORKING" });
      const held = container.querySelectorAll("[data-prop]");
      expect(held.length, prop).toBe(1);
      expect(held[0].getAttribute("data-prop"), prop).toBe(prop);
      expect(held[0].children.length, `${prop} is declared but draws nothing`).toBeGreaterThan(0);
      unmount();
    }
  });

  it("gives every prop a drawing of its own", () => {
    // A table with two entries that render identically is a table with one entry and a
    // misleading name — the owner cannot tell searching from checking.
    const drawings = new Map<string, string>();
    for (const prop of PROPS.filter((p) => p !== "NONE")) {
      const { container, unmount } = draw({ prop, posture: "WORKING" });
      const held = container.querySelector("[data-prop]")!.innerHTML;
      for (const [other, seen] of drawings) {
        expect(held, `${prop} is drawn the same as ${other}`).not.toBe(seen);
      }
      drawings.set(prop, held);
      unmount();
    }
    expect(drawings.size).toBe(PROPS.length - 1);
  });

  it("is held by the hand, inside the arm's own rotation", () => {
    // Outside it, the prop floats beside a limb that swings without it. The check is that
    // the prop lives *within* the rotated group rather than as a sibling of it.
    const { container } = draw({ prop: "BOOKS", posture: "WORKING" });
    const arm = [...container.querySelectorAll("g")].find((g) =>
      /rotate\(-?[\d.]+ 70 72\)/.test(g.getAttribute("transform") ?? ""),
    );
    expect(arm).toBeTruthy();
    expect(arm!.querySelector("[data-prop]")).not.toBeNull();
  });

  it("brings the arm down to carry something rather than leaving it swinging", () => {
    const angle = (over: Partial<Parameters<typeof Robot>[0]>) =>
      Number(
        draw(over).container.innerHTML.match(/rotate\((-?[\d.]+) 70 72\)/)?.[1] ?? NaN,
      );

    expect(angle({ prop: "CHART", posture: "WORKING" })).not.toBe(
      angle({ prop: "NONE", posture: "WORKING" }),
    );
  });
});

// ------------------------------------------------------------------------ §9, playful

describe("the playful flourishes", () => {
  it("lifts the robot off the ground to jump, and shrinks its shadow with it", () => {
    const shadow = (over: Partial<Parameters<typeof Robot>[0]>) => {
      const el = draw(over).container.querySelector("ellipse")!;
      return Number(el.getAttribute("rx"));
    };
    const body = (over: Partial<Parameters<typeof Robot>[0]>) =>
      draw(over).container.innerHTML.match(/translate\(0 (-?[\d.]+)\)/)?.[1];

    const grounded = { flourish: "NONE" as const, flourishAt: 0 };
    const airborne = { flourish: "JUMP" as const, flourishAt: 0.5 };

    expect(shadow(airborne)).toBeLessThan(shadow(grounded));
    expect(Number(body(airborne))).toBeLessThan(Number(body(grounded)));
  });

  it("starts and ends a jump on the ground", () => {
    const lift = (at: number) =>
      Number(draw({ flourish: "JUMP", flourishAt: at }).container.innerHTML.match(
        /translate\(0 (-?[\d.]+)\)/,
      )?.[1]);

    expect(lift(0)).toBe(0);
    expect(lift(1)).toBeCloseTo(0, 5);
    expect(lift(0.5)).toBeLessThan(-10);
  });

  it("moves the arm to wave, and moves it differently as the wave goes on", () => {
    const angle = (at: number) =>
      Number(draw({ flourish: "WAVE", flourishAt: at }).container.innerHTML.match(
        /rotate\((-?[\d.]+) 70 72\)/,
      )?.[1]);

    expect(angle(0.1)).not.toBe(angle(0.3));
    // Up, not down — a wave below the waist is not a wave.
    expect(angle(0.1)).toBeLessThan(-90);
  });

  it("does nothing at all when there is no flourish", () => {
    const still = draw({ flourish: "NONE", flourishAt: 0.5 }).container.innerHTML;
    const same = draw({ flourish: "NONE", flourishAt: 0.9 }).container.innerHTML;
    expect(still).toBe(same);
  });
});
