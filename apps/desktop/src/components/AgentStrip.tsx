import { useState } from "react";
import type { AgentStatus } from "@/lib/types";

const DOT: Record<AgentStatus["state"], string> = {
  working: "bg-state-working animate-pulse",
  completed: "bg-state-speaking",
  failed: "bg-state-error",
  waiting: "bg-state-idle",
};

/**
 * PART 64. Collapsed by default, and deliberately small.
 *
 * If the owner has to watch this strip to understand what is happening, the design has
 * failed: Thursday is supposed to be one assistant, not a fleet with a status board.
 */
export function AgentStrip({ agents }: { agents: AgentStatus[] }) {
  const [expanded, setExpanded] = useState(false);
  if (agents.length === 0) return null;

  return (
    <div className="border-t border-ink-800 px-6 py-2">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-2 text-left text-[11px] text-slate-500
                   hover:text-slate-400"
      >
        <span className="uppercase tracking-wider">working</span>
        <span className="flex gap-1.5">
          {agents.map((agent) => (
            <span key={agent.name} className={`h-1.5 w-1.5 rounded-full ${DOT[agent.state]}`} />
          ))}
        </span>
        <span className="ml-auto">{expanded ? "hide" : "details"}</span>
      </button>

      {expanded && (
        <ul className="mt-2 space-y-1">
          {agents.map((agent) => (
            <li key={agent.name} className="flex items-center gap-2 text-xs text-slate-400">
              <span className={`h-1.5 w-1.5 rounded-full ${DOT[agent.state]}`} />
              <span className="font-mono">{agent.name}</span>
              <span className="text-slate-600">{agent.state}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
