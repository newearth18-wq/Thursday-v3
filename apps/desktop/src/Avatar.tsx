import { useEffect, useRef, useState } from "react";
import { Robot } from "@/components/Robot";
import { useRealtime } from "@/hooks/useRealtime";
import {
  BUBBLE,
  BUBBLE_ABOVE,
  START,
  beat,
  bubbleAt,
  flourish,
  flourishProgress,
  gaitFor,
  stride,
  type Gait,
  type Walker,
} from "@/lib/avatar";
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
  // A clock that runs even when the walker does not — §8's blink and §14's visor pulse
  // both happen while the robot is standing perfectly still, so neither can be drawn from
  // the step cycle, which stops with the legs.
  const [clock, setClock] = useState(0);
  // The bubble is clamped to the screen rather than centred on the robot, so it needs the
  // width the robot is already being walked against.
  const [screen, setScreen] = useState(() => window.innerWidth);

  // A disconnected Thursday sits down. Wandering cheerfully around a screen while unable
  // to hear anything is the avatar equivalent of reporting success on no evidence.
  // §9. Sitting down is the one flourish that is also a way of standing still, so it is
  // resolved here where the gait is decided rather than drawn over the top of a walk — a
  // robot sitting while its legs keep striding is not sitting.
  const playing = connected ? flourish(clock, expression.mood, expression.posture) : "NONE";
  const gait: Gait = !connected
    ? "SIT"
    : playing === "SIT"
      ? "SIT"
      : gaitFor(expression.mood, expression.intensity, expression.posture);
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
        setClock((current) => beat(current, dt));
        setScreen(window.innerWidth);
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
      {/* The bubble is a sibling of the robot rather than a child, and clamped rather than
          centred on it. Centred, half of it hangs off the screen whenever the robot is near
          an edge — which is most of the time, because the edges are where it turns around. */}
      {says && (
        <div
          className="absolute rounded-2xl border px-3 py-1.5 text-center text-[11px]
                     leading-snug text-slate-100 backdrop-blur-md"
          style={{
            bottom: BUBBLE_ABOVE,
            left: bubbleAt(walker.x, screen),
            transform: "translateX(-50%)",
            maxWidth: BUBBLE,
            borderColor: withAlpha(glow, 0.45),
            background: withAlpha("#0a0f18", 0.82),
            boxShadow: `0 0 24px ${withAlpha(glow, 0.3)}`,
          }}
        >
          {says}
        </div>
      )}
      <div
        className="absolute bottom-3"
        // Positioned rather than animated in CSS: the walk is already a simulation, and a
        // transition on top of it would fight the physics and lag half a second behind.
        style={{ left: walker.x, transform: "translateX(-50%)" }}
      >
        <Robot
          mood={expression.mood}
          posture={expression.posture}
          // Straight from the socket, never from the gait or the mood. §10 is about what
          // the machine is doing with the microphone, and nothing this window computes is
          // allowed a say in it.
          listening={connected && expression.listening}
          prop={expression.prop}
          flourish={playing}
          flourishAt={flourishProgress(clock)}
          gait={gait}
          phase={walker.phase}
          clock={clock}
          facing={walker.facing}
        />
      </div>
    </div>
  );
}
