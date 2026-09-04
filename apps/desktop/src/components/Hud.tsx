import { useEffect, useState } from "react";
import { APPEARANCE } from "@/lib/mood";
import type { Device, Expression } from "@/lib/types";

/**
 * The readable half of the HUD (Sprint 81).
 *
 * The graph behind this is a canvas, because it is three hundred glows a second. Everything
 * a person has to *read* lives here in the DOM instead — text stays crisp, selectable, and
 * findable by a screen reader, none of which is true of words painted into a bitmap.
 *
 * Every string Thursday says about itself arrives already written from the server (Sprint
 * 80). The labels here are furniture — "devices", "waiting" — and name the instrument, not
 * the state.
 */

function Clock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  return (
    <div className="text-right">
      <div className="font-mono text-2xl tabular-nums tracking-widest text-slate-200">
        {now.toLocaleTimeString("th-TH", { hour12: false })}
      </div>
      <div className="text-[10px] uppercase tracking-[0.3em] text-slate-500">
        {now.toLocaleDateString("th-TH", { weekday: "long", day: "numeric", month: "short" })}
      </div>
    </div>
  );
}

/**
 * One count, as a bar.
 *
 * `of` is what the bar is full at, not a real ceiling — there is no maximum number of
 * things that can be waiting, and pretending otherwise would make five approvals and
 * fifty look identical. The number beside it is the truth; the bar is the glance.
 */
function Meter({
  label,
  value,
  of,
  tint,
}: {
  label: string;
  value: number;
  of: number;
  tint: string;
}) {
  const filled = Math.min(1, value / of);
  return (
    <div className="w-32">
      <div className="mb-1 flex items-baseline justify-between">
        <span className="text-[10px] uppercase tracking-[0.2em] text-slate-500">{label}</span>
        <span className="font-mono text-xs tabular-nums text-slate-300">{value}</span>
      </div>
      <div className="h-[3px] w-full rounded-full bg-white/5">
        <div
          className="h-full rounded-full transition-[width] duration-500"
          style={{ width: `${Math.max(filled * 100, value > 0 ? 8 : 0)}%`, background: tint }}
        />
      </div>
    </div>
  );
}

export function Hud({
  expression,
  devices,
  connected,
}: {
  expression: Expression;
  devices: Device[];
  connected: boolean;
}) {
  const look = APPEARANCE[expression.mood];

  return (
    <div className="pointer-events-none absolute inset-0 select-none">
      {/* ------------------------------------------------------------------- top left */}
      <div className="absolute left-8 top-7">
        <div className="flex items-center gap-2.5">
          <span
            className="h-2 w-2 rounded-full"
            style={{
              background: connected ? look.colour : "#475569",
              boxShadow: connected ? `0 0 12px ${look.colour}` : undefined,
            }}
          />
          <span className="text-sm font-medium tracking-[0.42em] text-slate-200">THURSDAY</span>
        </div>
        <p className="mt-2 max-w-[18rem] text-xs leading-relaxed text-slate-400">
          {connected ? expression.because : "กำลังเชื่อมต่อใหม่…"}
        </p>
      </div>

      {/* ------------------------------------------------------------------ top right */}
      <div className="absolute right-8 top-7">
        <Clock />
      </div>

      {/* ----------------------------------------------------------------- left column */}
      <div className="absolute left-8 top-1/2 -translate-y-1/2 space-y-4">
        <Meter label="working" value={expression.running} of={4} tint={look.colour} />
        <Meter label="waiting" value={expression.waiting} of={3} tint="#fbbf24" />
        <Meter label="faults" value={expression.unhealthy} of={3} tint="#f87171" />
      </div>

      {/* ---------------------------------------------------------------- right column */}
      <div className="absolute right-8 top-1/2 w-40 -translate-y-1/2 text-right">
        <div className="mb-2 text-[10px] uppercase tracking-[0.2em] text-slate-500">devices</div>
        {devices.length === 0 ? (
          <p className="text-xs text-slate-600">ยังไม่มีเครื่องเชื่อมต่อ</p>
        ) : (
          <ul className="space-y-1.5">
            {devices.slice(0, 6).map((device) => (
              <li key={device.id} className="flex items-center justify-end gap-2">
                <span className="truncate text-xs text-slate-300">{device.name}</span>
                <span
                  className="h-1.5 w-1.5 shrink-0 rounded-full"
                  style={{ background: device.status === "online" ? look.colour : "#475569" }}
                />
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* ------------------------------------------------------------ under the core */}
      {expression.activity && (
        <div className="absolute left-1/2 top-[calc(50%+7rem)] -translate-x-1/2 text-center">
          <p
            className="text-sm tracking-wide"
            style={{ color: look.colour, textShadow: `0 0 24px ${look.colour}55` }}
          >
            {expression.activity}
          </p>
        </div>
      )}
    </div>
  );
}
