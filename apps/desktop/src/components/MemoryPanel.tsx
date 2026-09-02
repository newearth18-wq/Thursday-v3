import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { MemoryRecord, PendingMemory } from "@/lib/types";

const LAYER_COLOUR: Record<string, string> = {
  preference: "text-thursday",
  semantic: "text-state-thinking",
  episodic: "text-slate-400",
  procedural: "text-state-working",
  project: "text-state-speaking",
  working: "text-slate-500",
};

/**
 * PART 69. Memory is not a black box.
 *
 * The owner can see what Thursday believes, where each belief came from, and delete any of
 * it. The pending list is the other half: a preference an agent proposed does not become a
 * belief until the owner says so (PART 76), so it is shown here before it is anything.
 */
export function MemoryPanel() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<MemoryRecord[]>([]);
  const [pending, setPending] = useState<PendingMemory[]>([]);
  const [conflicts, setConflicts] = useState<{ id: string; description: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const refreshPending = () =>
    Promise.all([
      api.memoryConfirmations().then((r) => setPending(r.pending)),
      api.memoryConflicts().then((r) => setConflicts(r.conflicts)),
    ]).catch(() => undefined);

  useEffect(() => {
    refreshPending();
    const timer = setInterval(refreshPending, 5000);
    return () => clearInterval(timer);
  }, []);

  const search = async (text: string) => {
    setBusy(true);
    try {
      setResults((await api.searchMemory(text)).memories);
    } catch (error) {
      setNote(String(error));
    } finally {
      setBusy(false);
    }
  };

  const forget = async (record: MemoryRecord) => {
    await api.forgetMemory(record.id);
    setResults((rows) => rows.filter((r) => r.id !== record.id));
    // Deleting is the one memory operation with no undo, so it says so rather than
    // pretending the record merely moved somewhere quieter.
    setNote(`forgotten — "${record.content.slice(0, 40)}…" is gone for good`);
  };

  return (
    <div className="space-y-4 p-4">
      {pending.length > 0 && (
        <section>
          <h3 className="mb-2 text-[11px] uppercase tracking-wider text-state-warning">
            waiting for you
          </h3>
          {pending.map((candidate) => (
            <div key={candidate.index} className="mb-2 rounded-lg bg-ink-900 p-2.5">
              <p className="text-xs text-slate-300">{candidate.content}</p>
              <p className="mt-1 text-[10px] text-slate-500">
                {candidate.layer.toLowerCase()}
                {candidate.proposed_by && ` · proposed by ${candidate.proposed_by}`}
              </p>
              <div className="mt-2 flex gap-3">
                <button
                  onClick={() => api.confirmMemory(candidate.index, true).then(refreshPending)}
                  className="text-[11px] text-thursday hover:text-thursday-light"
                >
                  remember this
                </button>
                <button
                  onClick={() => api.confirmMemory(candidate.index, false).then(refreshPending)}
                  className="text-[11px] text-slate-500 hover:text-slate-300"
                >
                  discard
                </button>
              </div>
            </div>
          ))}
        </section>
      )}

      {conflicts.length > 0 && (
        <section>
          <h3 className="mb-2 text-[11px] uppercase tracking-wider text-state-warning">
            contradictions
          </h3>
          {conflicts.map((conflict) => (
            <div key={conflict.id} className="mb-2 rounded-lg bg-ink-900 p-2.5">
              <p className="text-xs text-slate-400">{conflict.description}</p>
              <div className="mt-2 flex gap-3">
                {["kept_new", "kept_old", "both_valid"].map((resolution) => (
                  <button
                    key={resolution}
                    onClick={() =>
                      api.resolveConflict(conflict.id, resolution).then(refreshPending)
                    }
                    className="text-[11px] text-slate-500 hover:text-slate-200"
                  >
                    {resolution.replace("_", " ")}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </section>
      )}

      <section>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            search(query);
          }}
        >
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="what does Thursday remember about…"
            className="w-full rounded-lg bg-ink-900 px-3 py-2 text-xs text-slate-200 outline-none
                       placeholder:text-slate-600 focus:ring-1 focus:ring-thursday/40"
          />
        </form>

        {busy && <p className="mt-2 text-[11px] text-slate-600">searching…</p>}
        {note && <p className="mt-2 text-[11px] text-slate-500">{note}</p>}

        <ul className="mt-3 space-y-2">
          {results.map((record) => (
            <li key={record.id} className="rounded-lg bg-ink-900 p-2.5">
              <p className="text-xs text-slate-300">{record.content}</p>
              <div className="mt-1.5 flex items-center gap-2 text-[10px]">
                <span className={LAYER_COLOUR[record.layer.toLowerCase()] ?? "text-slate-500"}>
                  {record.layer.toLowerCase()}
                </span>
                <span className="text-slate-600">from {record.source.toLowerCase()}</span>
                <span className="text-slate-600">
                  confidence {Math.round(record.confidence * 100)}%
                </span>
                {record.pinned && <span className="text-thursday">pinned</span>}
                <button
                  onClick={() => forget(record)}
                  className="ml-auto text-slate-600 hover:text-state-error"
                >
                  forget
                </button>
              </div>
            </li>
          ))}
        </ul>

        {results.length === 0 && !busy && query !== "" && (
          <p className="mt-3 text-[11px] text-slate-600">nothing remembered about that</p>
        )}
      </section>
    </div>
  );
}
