import type { AvatarState } from "@/lib/types";

/**
 * PART 63/65. One orb, one state field.
 *
 * The owner should be able to read Thursday's condition from across the room without
 * focusing on it — which is why state is carried by colour and motion rather than text.
 */

const APPEARANCE: Record<AvatarState, { colour: string; label: string; animate: string }> = {
  IDLE: { colour: "bg-state-idle", label: "ready", animate: "" },
  LISTENING: { colour: "bg-state-listening", label: "listening", animate: "animate-breathe" },
  THINKING: { colour: "bg-state-thinking", label: "thinking", animate: "animate-pulse-slow" },
  WORKING: { colour: "bg-state-working", label: "working", animate: "animate-pulse-slow" },
  SPEAKING: { colour: "bg-state-speaking", label: "speaking", animate: "animate-breathe" },
  WAITING_APPROVAL: { colour: "bg-state-warning", label: "waiting for you", animate: "animate-pulse" },
  WARNING: { colour: "bg-state-warning", label: "attention", animate: "" },
  ERROR: { colour: "bg-state-error", label: "error", animate: "" },
};

export function Orb({ state, connected }: { state: AvatarState; connected: boolean }) {
  const look = APPEARANCE[state];
  return (
    <div className="flex flex-col items-center gap-3 py-6">
      <div className="relative h-24 w-24">
        <div className={`absolute inset-0 rounded-full opacity-40 blur-2xl ${look.colour}`} />
        <div className={`absolute inset-2 rounded-full ${look.colour} ${look.animate}`} />
        <div className="absolute inset-[1.15rem] rounded-full bg-ink-950/60 backdrop-blur-sm" />
      </div>
      <div className="text-center">
        <div className="text-sm font-medium tracking-wide text-slate-300">Thursday</div>
        <div className="text-xs text-slate-500">{connected ? look.label : "reconnecting…"}</div>
      </div>
    </div>
  );
}
