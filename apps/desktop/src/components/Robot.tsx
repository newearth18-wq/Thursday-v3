import {
  EYES,
  MIC,
  POSE,
  SPEED,
  blinking,
  visorPulse,
  type Gait,
} from "@/lib/avatar";
import { APPEARANCE, withAlpha } from "@/lib/mood";
import type { Mood, Posture } from "@/lib/types";

/**
 * The robot (Sprint 82). One drawing, posed by a number.
 *
 * Everything that moves is a function of `phase` — the step cycle from `stride()` — so the
 * legs match the ground rather than sliding along it, and the arms swing against them the
 * way a person's do. The colour is the mood's, the same table the HUD's core is drawn from,
 * so the two windows cannot show different feelings at the same moment.
 *
 * Deliberately drawn rather than animated frame by frame: a sprite sheet would fix the
 * number of moods and gaits at whatever somebody drew, and this has to cover nine of one
 * and five of the other — now times six postures, which a sprite sheet would have made
 * impossible.
 *
 * Sprint 85 added the second axis. `mood` still chooses the colour and the eyes; `posture`
 * chooses what the body is doing, and the two are drawn together — a Thursday that is
 * listening while the last job failed shows a worried face *and* an attentive body, which
 * one enum could not have said.
 *
 * Two rules from the addendum are structural here rather than stylistic:
 *
 *  - **There is no mouth** (§14). Nothing in this file draws one, and speech is a band of
 *    light across the visor.
 *  - **The recording indicator answers to nothing** (§10). It is drawn outside the head
 *    group, outside every posture branch, and in a colour that is not in the mood table,
 *    so no feeling and no pose can take it away.
 */

/** The eyes, as paths. Shapes only — the bubble does the talking. */
function Eyes({ mood, glow, shut }: { mood: Mood; glow: string; shut?: boolean }) {
  // A blink closes whatever the eyes were doing. Drawn by borrowing the SHUT shape rather
  // than by a separate lid, so there is one closed eye in this file and not two that drift.
  const shape = shut ? "SHUT" : EYES[mood];
  const light = { fill: glow, filter: "url(#eyeGlow)" };

  if (shape === "SHUT") {
    return (
      <g stroke={glow} strokeWidth="2.6" strokeLinecap="round" filter="url(#eyeGlow)">
        <path d="M34 52 h9" />
        <path d="M57 52 h9" />
      </g>
    );
  }
  if (shape === "HAPPY") {
    return (
      <g
        stroke={glow}
        strokeWidth="2.8"
        strokeLinecap="round"
        fill="none"
        filter="url(#eyeGlow)"
      >
        <path d="M34 54 q4.5 -7 9 0" />
        <path d="M57 54 q4.5 -7 9 0" />
      </g>
    );
  }
  if (shape === "WORRIED") {
    return (
      <>
        <ellipse cx="38.5" cy="53" rx="3.6" ry="3.2" {...light} />
        <ellipse cx="61.5" cy="53" rx="3.6" ry="3.2" {...light} />
        <g stroke={glow} strokeWidth="2" strokeLinecap="round" opacity="0.75">
          <path d="M33 45 l8 3.5" />
          <path d="M67 45 l-8 3.5" />
        </g>
      </>
    );
  }
  const radius = shape === "WIDE" ? 5.4 : 4.2;
  return (
    <>
      <ellipse cx="38.5" cy="52" rx={radius} ry={radius * 1.08} {...light} />
      <ellipse cx="61.5" cy="52" rx={radius} ry={radius * 1.08} {...light} />
      <circle cx="40.2" cy="50" r="1.5" fill="#ffffff" opacity="0.9" />
      <circle cx="63.2" cy="50" r="1.5" fill="#ffffff" opacity="0.9" />
    </>
  );
}

export function Robot({
  mood,
  posture,
  listening,
  gait,
  phase,
  clock = 0,
  facing,
  size = 152,
}: {
  mood: Mood;
  /** Sprint 85. What the body is doing — §8, §10–§12, §14, §20. */
  posture: Posture;
  /** §10. Drawn unconditionally, because a recording light answers to nothing. */
  listening: boolean;
  gait: Gait;
  phase: number;
  /** Seconds, from `beat()`. Runs when `phase` does not, so a still robot still blinks. */
  clock?: number;
  facing: 1 | -1;
  size?: number;
}) {
  const glow = APPEARANCE[mood].colour;
  const pose = POSE[posture];
  const cycle = phase * Math.PI * 2;
  const moving = SPEED[gait] > 0;
  const sitting = gait === "SIT";
  // §8: an idle robot must not freeze. The blink is the smallest thing that says the
  // machine is still running when nothing else is moving, and it needs its own clock
  // because `phase` deliberately stops with the legs.
  const blink = blinking(clock) && !pose.resting;
  // §14: no mouth anywhere in this drawing. Speech is a band of light across the visor,
  // which is also why it needs `clock` — Thursday stands still to speak, so `phase` is
  // frozen at zero for the whole utterance.
  const speech = pose.visor === "pulse" ? visorPulse(clock) : 0;

  // Amplitudes scale with the gait, so a run is a run rather than a fast walk.
  const swing = moving ? (gait === "RUN" ? 26 : gait === "WALK" ? 15 : 7) : 0;
  const bob = moving ? Math.abs(Math.sin(cycle)) * (gait === "RUN" ? 4 : 2.2) : 0;
  // Leaning into it. A body that stays vertical at speed reads as sliding.
  const lean = gait === "RUN" ? 7 : gait === "WALK" ? 2.5 : 0;

  const legFront = Math.sin(cycle) * swing;
  const legBack = -legFront;
  const armFront = -legFront * 0.8;
  const armBack = -legBack * 0.8;

  // Waiting: it turns to face you and lifts a hand. The one pose that is a request (§18).
  // The posture can call for the same arm — §11's hand near the chin — so the two are
  // resolved here rather than in two places that could both claim the arm at once.
  const hand: "none" | "chin" | "raised" =
    pose.hand !== "none"
      ? pose.hand
      : gait === "ALERT" && (mood === "WAITING" || mood === "ATTENTIVE")
        ? "raised"
        : "none";
  const armAngle = hand === "chin" ? -152 : hand === "raised" ? -118 : armFront;

  return (
    <svg
      width={size}
      height={size * 1.2}
      viewBox="0 0 100 120"
      // The whole drawing mirrors, so there is one robot rather than two drawings that
      // drift apart the first time either is edited.
      style={{ transform: `scaleX(${facing})`, overflow: "visible" }}
      aria-hidden="true"
    >
      <defs>
        <filter id="eyeGlow" x="-120%" y="-120%" width="340%" height="340%">
          <feGaussianBlur stdDeviation="2.2" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
        <linearGradient id="shell" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#ffffff" />
          <stop offset="62%" stopColor="#eef2f8" />
          <stop offset="100%" stopColor="#cdd6e4" />
        </linearGradient>
        <radialGradient id="visor" cx="0.5" cy="0.35" r="0.8">
          <stop offset="0%" stopColor="#243047" />
          <stop offset="100%" stopColor="#0d1420" />
        </radialGradient>
      </defs>

      {/* The shadow stays on the ground while the body bobs — that is what makes the bob
          read as bouncing rather than as the whole picture sliding up and down. */}
      <ellipse
        cx="50"
        cy="116"
        rx={sitting ? 26 : 20 - bob}
        ry={4 - bob * 0.4}
        fill="#000000"
        opacity="0.35"
      />

      <g
        transform={`translate(0 ${-bob}) rotate(${lean * -1 + pose.lean} 50 100)`}
        opacity={sitting ? 0.85 : 1}
      >
        {/* ------------------------------------------------------------------- the legs */}
        {sitting ? (
          <g fill="url(#shell)" stroke="#b8c2d2" strokeWidth="1">
            <rect x="28" y="98" width="20" height="12" rx="6" />
            <rect x="52" y="98" width="20" height="12" rx="6" />
          </g>
        ) : (
          <g fill="url(#shell)" stroke="#b8c2d2" strokeWidth="1">
            <g transform={`rotate(${legBack} 43 92)`}>
              <rect x="38.5" y="90" width="9.5" height="24" rx="4.7" />
            </g>
            <g transform={`rotate(${legFront} 57 92)`}>
              <rect x="52" y="90" width="9.5" height="24" rx="4.7" />
            </g>
          </g>
        )}

        {/* ------------------------------------------------------------------- the arms */}
        <g fill="url(#shell)" stroke="#b8c2d2" strokeWidth="1">
          <g transform={`rotate(${armBack} 30 72)`}>
            <rect x="25.5" y="69" width="9" height="23" rx="4.5" />
          </g>
          <g transform={`rotate(${armAngle} 70 72)`}>
            <rect x="65.5" y="69" width="9" height="23" rx="4.5" />
          </g>
        </g>

        {/* ------------------------------------------------------------------- the body */}
        <rect
          x="28"
          y="64"
          width="44"
          height="36"
          rx="15"
          fill="url(#shell)"
          stroke="#b8c2d2"
          strokeWidth="1.2"
        />
        {/* The chest light. It is the same colour as the eyes and the HUD's core, which is
            the whole point: one mood, drawn in three places. */}
        <circle cx="50" cy="80" r="5.5" fill={withAlpha(glow, 0.28)} />
        <circle cx="50" cy="80" r="3" fill={glow} filter="url(#eyeGlow)" />

        {/* ------------------------------------------------------------------- the head */}
        <g transform={`rotate(${(moving ? Math.sin(cycle) * 2 : 0) + pose.tilt} 50 46)`}>
          {/* antenna */}
          <path d="M50 24 v-8" stroke="#b8c2d2" strokeWidth="2" strokeLinecap="round" />
          <circle cx="50" cy="13" r="3.2" fill={glow} filter="url(#eyeGlow)" />

          <rect
            x="24"
            y="24"
            width="52"
            height="40"
            rx="16"
            fill="url(#shell)"
            stroke="#b8c2d2"
            strokeWidth="1.2"
          />
          {/* ear cups, the reference images' one distinctive feature */}
          <rect x="18" y="38" width="8" height="14" rx="4" fill="#dbe2ec" stroke="#b8c2d2" />
          <rect x="74" y="38" width="8" height="14" rx="4" fill="#dbe2ec" stroke="#b8c2d2" />

          <rect x="29" y="35" width="42" height="24" rx="12" fill="url(#visor)" />
          <Eyes mood={mood} glow={glow} shut={blink} />
          {/* §14. The light band that stands in for a mouth: it widens and brightens with
              the voice and vanishes entirely when Thursday is not speaking. Drawn over the
              visor rather than under the eyes so it reads as the face lighting up. */}
          {pose.visor === "pulse" && (
            <rect
              x={50 - (7 + speech * 9)}
              y="56"
              width={(7 + speech * 9) * 2}
              height="2.6"
              rx="1.3"
              fill={glow}
              opacity={0.45 + speech * 0.55}
              filter="url(#eyeGlow)"
            />
          )}
        </g>

        {/* §10, the recording indicator. Outside every branch above on purpose: it is not
            inside the head group (which tilts), not inside a posture test, and not drawn in
            a mood colour — a light that says the microphone is open must not be something a
            mood, a pose or a head turn can take away. `data-listening` is what
            `Robot.test.tsx` walks, so the guarantee is checked and not merely intended. */}
        <g data-listening={listening ? "true" : "false"}>
          {listening && (
            <>
              <circle cx="79" cy="27" r="8" fill={MIC} opacity="0.18" />
              <circle cx="79" cy="27" r="4.2" fill={MIC} filter="url(#eyeGlow)" />
            </>
          )}
        </g>
      </g>
    </svg>
  );
}
