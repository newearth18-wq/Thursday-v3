import { EYES, SPEED, type Gait } from "@/lib/avatar";
import { APPEARANCE, withAlpha } from "@/lib/mood";
import type { Mood } from "@/lib/types";

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
 * and five of the other.
 */

/** The eyes, as paths. Shapes only — the bubble does the talking. */
function Eyes({ mood, glow }: { mood: Mood; glow: string }) {
  const shape = EYES[mood];
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
  gait,
  phase,
  facing,
  size = 152,
}: {
  mood: Mood;
  gait: Gait;
  phase: number;
  facing: 1 | -1;
  size?: number;
}) {
  const glow = APPEARANCE[mood].colour;
  const cycle = phase * Math.PI * 2;
  const moving = SPEED[gait] > 0;
  const sitting = gait === "SIT";

  // Amplitudes scale with the gait, so a run is a run rather than a fast walk.
  const swing = moving ? (gait === "RUN" ? 26 : gait === "WALK" ? 15 : 7) : 0;
  const bob = moving ? Math.abs(Math.sin(cycle)) * (gait === "RUN" ? 4 : 2.2) : 0;
  // Leaning into it. A body that stays vertical at speed reads as sliding.
  const lean = gait === "RUN" ? 7 : gait === "WALK" ? 2.5 : 0;

  const legFront = Math.sin(cycle) * swing;
  const legBack = -legFront;
  const armFront = -legFront * 0.8;
  const armBack = -legBack * 0.8;

  // Waiting: it turns to face you and lifts a hand. The one pose that is a request.
  const asking = gait === "ALERT" && (mood === "WAITING" || mood === "ATTENTIVE");

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

      <g transform={`translate(0 ${-bob}) rotate(${lean * -1} 50 80)`} opacity={sitting ? 0.85 : 1}>
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
          <g transform={`rotate(${asking ? -118 : armFront} 70 72)`}>
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
        <g transform={`rotate(${moving ? Math.sin(cycle) * 2 : 0} 50 46)`}>
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
          <Eyes mood={mood} glow={glow} />
        </g>
      </g>
    </svg>
  );
}
