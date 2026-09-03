import { api } from "@/lib/api";
import type { Approval } from "@/lib/types";

/**
 * PART 38/70. The full context comes before the buttons, never after — the owner should
 * never have to click to find out what they are approving.
 */
export function ApprovalDialog({
  approval,
  onResolved,
}: {
  approval: Approval;
  onResolved: (id: string) => void;
}) {
  // ADR 0008: an ASK_ALWAYS action offers only a one-time answer. The UI does not invent
  // "always allow" for something the engine would refuse to remember anyway.
  const offersAlways = (approval.scopes_offered ?? ["once"]).includes("always");

  const decide = async (action: "approve" | "reject", scope = "once") => {
    if (action === "approve") await api.approve(approval.id, scope);
    else await api.reject(approval.id);
    onResolved(approval.id);
  };

  return (
    <div className="mx-6 mb-3 rounded-xl border border-state-warning/40 bg-state-warning/5 p-4">
      <div className="mb-2 flex items-center gap-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-state-warning">
          needs your approval
        </span>
        <span className="rounded bg-ink-800 px-1.5 py-0.5 text-[11px] text-slate-400">
          risk {approval.risk.toLowerCase()}
        </span>
        {!approval.reversible && (
          <span className="rounded bg-state-error/20 px-1.5 py-0.5 text-[11px] text-state-error">
            cannot be undone
          </span>
        )}
      </div>

      <dl className="grid grid-cols-[5.5rem_1fr] gap-x-3 gap-y-1 text-xs">
        <dt className="text-slate-500">action</dt>
        <dd className="font-mono text-slate-200">{approval.action}</dd>
        <dt className="text-slate-500">on</dt>
        <dd className="truncate font-mono text-slate-300">{approval.resource || "—"}</dd>
        <dt className="text-slate-500">device</dt>
        <dd className="text-slate-300">{approval.device_name ?? "—"}</dd>
        {approval.agent && (
          <>
            <dt className="text-slate-500">agent</dt>
            <dd className="text-slate-300">{approval.agent}</dd>
          </>
        )}
        <dt className="text-slate-500">expected</dt>
        <dd className="text-slate-300">{approval.expected_outcome}</dd>
        <dt className="text-slate-500">if you say no</dt>
        <dd className="text-slate-400">{approval.consequence_of_refusal}</dd>
      </dl>

      <div className="mt-3 flex gap-2">
        <button
          onClick={() => decide("approve")}
          className="rounded-lg bg-state-speaking/20 px-3 py-1.5 text-xs font-medium
                     text-state-speaking hover:bg-state-speaking/30"
        >
          Approve once
        </button>
        {offersAlways && (
          <button
            onClick={() => decide("approve", "always")}
            className="rounded-lg bg-ink-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-ink-600"
            title="Scoped to this directory and expiring — never a blanket permission"
          >
            Always allow
          </button>
        )}
        <button
          onClick={() => decide("reject")}
          className="rounded-lg bg-ink-800 px-3 py-1.5 text-xs text-slate-400 hover:bg-ink-700"
        >
          Reject
        </button>
      </div>
    </div>
  );
}
