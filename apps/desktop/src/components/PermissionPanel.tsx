import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { Grant, Policy } from "@/lib/types";

const DECISIONS = ["AUTO", "ASK_ONCE", "ASK_ALWAYS", "BLOCK"] as const;

const DECISION_LABEL: Record<string, string> = {
  AUTO: "just do it",
  ASK_ONCE: "ask, then remember",
  ASK_ALWAYS: "ask every time",
  BLOCK: "never",
};

const DECISION_COLOUR: Record<string, string> = {
  AUTO: "text-state-speaking",
  ASK_ONCE: "text-slate-300",
  ASK_ALWAYS: "text-state-warning",
  BLOCK: "text-state-error",
};

const AUTONOMY = ["SUGGEST_ONLY", "SAFE_ACTIONS", "MODERATE", "HIGH"] as const;

/**
 * PART 70. Every permission in one place, in the owner's words.
 *
 * Two things this panel refuses to do. It does not offer a control that would not take
 * effect — an ASK_ALWAYS action shows its mode and no dropdown, because a setting that
 * silently reverts teaches the owner something false about their own machine. And it never
 * hides a standing grant: anything Thursday may do without asking again is listed, with the
 * revoke next to it.
 */
export function PermissionPanel() {
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [grants, setGrants] = useState<Grant[]>([]);
  const [autonomy, setAutonomy] = useState("MODERATE");
  const [filter, setFilter] = useState("");
  const [error, setError] = useState<string | null>(null);

  const refresh = () =>
    Promise.all([
      api.policies().then((r) => {
        setPolicies(r.policies);
        setAutonomy(r.autonomy);
      }),
      api.grants().then((r) => setGrants(r.grants as Grant[])),
    ]).catch((e) => setError(String(e)));

  useEffect(() => {
    refresh();
  }, []);

  const grouped = useMemo(() => {
    const match = filter.trim().toLowerCase();
    const rows = match ? policies.filter((p) => p.action.includes(match)) : policies;
    const buckets = new Map<string, Policy[]>();
    for (const policy of rows) {
      buckets.set(policy.namespace, [...(buckets.get(policy.namespace) ?? []), policy]);
    }
    return [...buckets.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [policies, filter]);

  const change = async (policy: Policy, decision: string) => {
    setError(null);
    try {
      await api.setPolicy(policy.action, decision);
      await refresh();
    } catch (e) {
      // The server refuses a change it would not honour; showing why beats a silent revert.
      setError(String(e).replace(/^Error: \d+ [^:]+: /, ""));
    }
  };

  return (
    <div className="space-y-4 p-4">
      <section>
        <h3 className="mb-2 text-[11px] uppercase tracking-wider text-slate-500">how much rope</h3>
        <div className="flex flex-wrap gap-1">
          {AUTONOMY.map((level) => (
            <button
              key={level}
              onClick={() => api.setAutonomy(level).then(refresh)}
              className={`rounded px-2 py-1 text-[11px] ${
                autonomy === level
                  ? "bg-thursday/20 text-thursday"
                  : "bg-ink-900 text-slate-500 hover:text-slate-300"
              }`}
            >
              {level.replace("_", " ").toLowerCase()}
            </button>
          ))}
        </div>
        <p className="mt-1.5 text-[10px] leading-relaxed text-slate-600">
          Raising this only relaxes actions that were already automatic. Anything that asks
          every time keeps asking, at every level.
        </p>
      </section>

      {grants.length > 0 && (
        <section>
          <h3 className="mb-2 text-[11px] uppercase tracking-wider text-slate-500">
            standing permissions
          </h3>
          <ul className="space-y-1">
            {grants.map((grant) => (
              <li key={grant.id} className="flex items-center gap-2 rounded bg-ink-900 px-2 py-1.5">
                <span className="font-mono text-[11px] text-slate-300">{grant.action}</span>
                <span className="truncate font-mono text-[10px] text-slate-600">
                  {grant.resource_glob}
                </span>
                <button
                  onClick={() => api.revokeGrant(grant.id).then(refresh)}
                  className="ml-auto text-[10px] text-slate-600 hover:text-state-error"
                >
                  revoke
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section>
        <input
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
          placeholder="filter actions"
          className="mb-3 w-full rounded-lg bg-ink-900 px-3 py-1.5 text-xs text-slate-200 outline-none
                     placeholder:text-slate-600 focus:ring-1 focus:ring-thursday/40"
        />

        {error && <p className="mb-2 text-[11px] text-state-warning">{error}</p>}

        {grouped.map(([namespace, rows]) => (
          <div key={namespace} className="mb-3">
            <h4 className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-600">
              {namespace}
            </h4>
            <ul className="space-y-0.5">
              {rows.map((policy) => (
                <li key={policy.action} className="flex items-center gap-2 px-1 py-1">
                  <span className="font-mono text-[11px] text-slate-400">
                    {policy.action.slice(namespace.length + 1)}
                  </span>
                  {!policy.reversible && (
                    <span className="text-[10px] text-slate-600">no undo</span>
                  )}

                  {policy.blocked || !policy.can_relax ? (
                    <span
                      className={`ml-auto text-[11px] ${DECISION_COLOUR[policy.decision]}`}
                      title={
                        policy.blocked
                          ? "blocked outright — there is no setting for this"
                          : "this one always asks; the setting cannot be relaxed"
                      }
                    >
                      {DECISION_LABEL[policy.decision]}
                    </span>
                  ) : (
                    <select
                      value={policy.decision}
                      onChange={(event) => change(policy, event.target.value)}
                      className="ml-auto rounded bg-ink-900 px-1.5 py-0.5 text-[11px] text-slate-300
                                 outline-none focus:ring-1 focus:ring-thursday/40"
                    >
                      {DECISIONS.map((decision) => (
                        <option key={decision} value={decision}>
                          {DECISION_LABEL[decision]}
                        </option>
                      ))}
                    </select>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </section>
    </div>
  );
}
