import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Task } from "@/lib/types";

const TERMINAL = new Set(["COMPLETED", "FAILED", "CANCELLED"]);

const STATUS_COLOUR: Record<string, string> = {
  RUNNING: "text-state-working",
  VERIFYING: "text-state-thinking",
  WAITING_APPROVAL: "text-state-warning",
  BLOCKED: "text-state-error",
  PAUSED: "text-slate-400",
  COMPLETED: "text-state-speaking",
  FAILED: "text-state-error",
  CANCELLED: "text-slate-500",
};

/** PART 67. Title, progress, current state, and a way out of it. */
export function TaskPanel() {
  const [tasks, setTasks] = useState<Task[]>([]);

  const refresh = () => api.tasks().then((r) => setTasks(r.tasks)).catch(() => undefined);
  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 3000);
    return () => clearInterval(timer);
  }, []);

  const active = tasks.filter((t) => !TERMINAL.has(t.status));
  const recent = tasks.filter((t) => TERMINAL.has(t.status)).slice(0, 5);

  return (
    <div className="space-y-4 p-4">
      <section>
        <h3 className="mb-2 text-[11px] uppercase tracking-wider text-slate-500">active</h3>
        {active.length === 0 && <p className="text-xs text-slate-600">nothing running</p>}
        {active.map((task) => (
          <div key={task.id} className="mb-2 rounded-lg bg-ink-900 p-2.5">
            <div className="flex items-start justify-between gap-2">
              <span className="text-xs text-slate-300">{task.title}</span>
              <span className={`text-[10px] uppercase ${STATUS_COLOUR[task.status] ?? "text-slate-500"}`}>
                {task.status.replace("_", " ").toLowerCase()}
              </span>
            </div>
            <div className="mt-2 h-1 overflow-hidden rounded bg-ink-700">
              <div
                className="h-full bg-thursday transition-all"
                style={{ width: `${Math.round(task.progress * 100)}%` }}
              />
            </div>
            <div className="mt-2 flex gap-2">
              {task.status === "PAUSED" ? (
                <button onClick={() => api.resumeTask(task.id).then(refresh)}
                        className="text-[11px] text-slate-400 hover:text-slate-200">resume</button>
              ) : (
                <button onClick={() => api.pauseTask(task.id).then(refresh)}
                        className="text-[11px] text-slate-400 hover:text-slate-200">pause</button>
              )}
              <button onClick={() => api.cancelTask(task.id).then(refresh)}
                      className="text-[11px] text-slate-500 hover:text-state-error">cancel</button>
            </div>
          </div>
        ))}
      </section>

      {recent.length > 0 && (
        <section>
          <h3 className="mb-2 text-[11px] uppercase tracking-wider text-slate-500">recent</h3>
          <ul className="space-y-1">
            {recent.map((task) => (
              <li key={task.id} className="flex items-center justify-between gap-2 text-xs">
                <span className="truncate text-slate-500">{task.title}</span>
                <span className={STATUS_COLOUR[task.status] ?? "text-slate-600"}>
                  {task.status.toLowerCase()}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
