import { useEffect, useState } from "react";
import { LABEL_HEIGHT, LABEL_WIDTH, type Spot, arrow, locate, place } from "@/lib/spotlight";

/**
 * §13 — the arrow, on the real control.
 *
 * An arrow is a claim about where something is, so this component's most important behaviour
 * is what it does when the claim cannot be made: a control that is not on screen — a closed
 * drawer, a button that only appears while something runs, a control renamed since the lesson
 * was written — gets **no arrow and a sentence saying so**. There is no "draw it roughly
 * there" path, because roughly there is wrong and wrong once is enough.
 *
 * It points; it does not press. Nothing here clicks the control, focuses it, or fills it in.
 * The owner doing the thing is the lesson — a walkthrough that performed the step would be
 * the "mark as complete" button of ADR 0065's Learning Center, wearing a different shape.
 */
export function Spotlight({
  name,
  label,
  onUnavailable,
}: {
  name: string;
  label: string;
  onUnavailable?: (missing: boolean) => void;
}) {
  const [spot, setSpot] = useState<Spot | null>(null);

  useEffect(() => {
    if (!name) {
      setSpot(null);
      onUnavailable?.(false);
      return;
    }

    // Re-measured on a timer rather than once: a drawer opens, the window resizes, the
    // control moves. A ring left where the button used to be is the same lie as an arrow
    // pointing at nothing.
    const measure = () => {
      const target = locate(name);
      setSpot(target ? place(target, { width: innerWidth, height: innerHeight }) : null);
      onUnavailable?.(target === null);
    };
    measure();
    const timer = window.setInterval(measure, 500);
    window.addEventListener("resize", measure);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("resize", measure);
    };
  }, [name, onUnavailable]);

  if (!spot) return null;

  return (
    <svg
      data-testid="spotlight"
      aria-hidden
      className="pointer-events-none fixed inset-0 z-50"
      width="100%"
      height="100%"
    >
      <rect
        x={spot.ring.x}
        y={spot.ring.y}
        width={spot.ring.width}
        height={spot.ring.height}
        rx={10}
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        className="text-thursday"
      />
      <path
        d={arrow(spot)}
        stroke="currentColor"
        strokeWidth={2}
        fill="none"
        className="text-thursday"
      />
      <foreignObject
        x={spot.label.x}
        y={spot.label.y}
        width={LABEL_WIDTH}
        height={LABEL_HEIGHT}
      >
        <div className="rounded-lg bg-thursday/90 px-2 py-1.5 text-[11px] leading-snug text-white">
          {label}
        </div>
      </foreignObject>
    </svg>
  );
}
