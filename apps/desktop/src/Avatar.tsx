import { useEffect, useRef, useState } from "react";
import { Robot } from "@/components/Robot";
import { useRealtime } from "@/hooks/useRealtime";
import { START, gaitFor, stride, type Gait, type Walker } from "@/lib/avatar";
import { APPEARANCE, withAlpha } from "@/lib/mood";

/**
 * The avatar window (Sprint 82).
 *
 * What the owner sees when they are working somewhere else: a small robot along the bottom
 * of the screen that walks about, runs when Thursday is busy, sits down when everything is
 * stopped, and turns to face them when something is waiting on their answer.
 *
 * It is a second window onto the *same* expression, not a second opinion about it. Mood,
 * colour and the sentence in the bubble all arrive from the socket already decided (ADR
 * 0054), so the robot and the HUD can never be showing different feelings at once — which
 * they would within a week if either computed its own.
 *
 * It says only what Thursday said. There is no phrase table here, and the one thing this
 * window adds to the conversation is its own silence when disconnected.
 */
export default function Avatar() {
  const { expression, connected } = useRealtime();
  const [walker, setWalker] = useState<Walker>(START);

  // A disconnected Thursday sits down. Wandering cheerfully around a screen while unable
  // to hear anything is the avatar equivalent of reporting success on no evidence.
  const gait: Gait = connected ? gaitFor(expression.mood, expression.intensity) : "SIT";
  const latest = useRef(gait);
  latest.current = gait;

  useEffect(() => {
    let frame = 0;
    let previous = performance.now();
    const still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

    const tick = (now: number) => {
      const dt = (now - previous) / 16.667;
      previous = now;
      if (!still) {
        setWalker((current) => stride(current, latest.current, window.innerWidth, dt));
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, []);

  const glow = APPEARANCE[expression.mood].colour;
  const says = connected ? expression.activity || expression.because : "";

  return (
    <div className="pointer-events-none fixed inset-0 overflow-hidden bg-transparent">
      <div
        className="absolute bottom-3"
        // Positioned rather than animated in CSS: the walk is already a simulation, and a
        // transition on top of it would fight the physics and lag half a second behind.
        style={{ left: walker.x, transform: "translateX(-50%)" }}
      >
        {says && (
          <div
            className="mx-auto mb-1 max-w-[15rem] rounded-2xl border px-3 py-1.5 text-center
                       text-[11px] leading-snug text-slate-100 backdrop-blur-md"
            style={{
              borderColor: withAlpha(glow, 0.45),
              background: withAlpha("#0a0f18", 0.82),
              boxShadow: `0 0 24px ${withAlpha(glow, 0.3)}`,
            }}
          >
            {says}
          </div>
        )}
        <Robot mood={expression.mood} gait={gait} phase={walker.phase} facing={walker.facing} />
      </div>
    </div>
  );
}
