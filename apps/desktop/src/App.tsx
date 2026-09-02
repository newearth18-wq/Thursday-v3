import { useEffect, useState } from "react";
import { AgentStrip } from "@/components/AgentStrip";
import { ApprovalDialog } from "@/components/ApprovalDialog";
import { Conversation } from "@/components/Conversation";
import { DevicePanel } from "@/components/DevicePanel";
import { MemoryPanel } from "@/components/MemoryPanel";
import { Orb } from "@/components/Orb";
import { PermissionPanel } from "@/components/PermissionPanel";
import { TaskPanel } from "@/components/TaskPanel";
import { useRealtime } from "@/hooks/useRealtime";
import { api } from "@/lib/api";

const DRAWERS = {
  tasks: { label: "tasks", render: () => <TaskPanel /> },
  devices: { label: "devices", render: () => <DevicePanel /> },
  memory: { label: "memory", render: () => <MemoryPanel /> },
  permissions: { label: "permissions", render: () => <PermissionPanel /> },
} as const;

type Drawer = keyof typeof DRAWERS;

/**
 * PART 63/64. One window, one identity.
 *
 * The conversation is the whole interface. Tasks, devices, memory and permissions are
 * drawers the owner opens when they want them — not dashboards they must watch. If the
 * right-hand side has to be open for Thursday to make sense, the design has failed.
 */
export default function App() {
  const { connected, messages, avatar, approvals, agents, thinking, send, interrupt, setApprovals } =
    useRealtime();
  const [draft, setDraft] = useState("");
  const [drawer, setDrawer] = useState<Drawer | null>(null);
  const [lockdown, setLockdown] = useState(false);

  // The tray can stop everything without the window being involved (see src-tauri). When
  // it does, the window must not go on claiming all is well.
  useEffect(() => {
    let unlisten: (() => void) | undefined;
    import("@tauri-apps/api/event")
      .then(({ listen }) => listen("emergency.stopped", () => setLockdown(true)))
      .then((off) => {
        unlisten = off;
      })
      .catch(() => undefined); // running in a plain browser; there is no tray to hear from
    return () => unlisten?.();
  }, []);

  // Escape interrupts. Not a nicety: PART 98 says stopping must never be buried in a menu.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") interrupt();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [interrupt]);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const text = draft.trim();
    if (!text) return;
    send(text);
    setDraft("");
  };

  const stopEverything = async () => {
    await api.emergencyStop();
    setLockdown(true);
  };

  return (
    <div className="flex h-screen bg-ink-950 text-slate-200">
      <main className="flex min-w-0 flex-1 flex-col">
        <Orb state={avatar} connected={connected} />

        <AgentStrip agents={agents} />

        <Conversation messages={messages} thinking={thinking} />

        {approvals.map((approval) => (
          <ApprovalDialog
            key={approval.id}
            approval={approval}
            onResolved={(id) => setApprovals((prior) => prior.filter((a) => a.id !== id))}
          />
        ))}

        {lockdown && (
          <div className="mx-6 mb-3 rounded-xl border border-state-error/50 bg-state-error/10 p-3">
            <p className="text-xs text-state-error">
              Everything is stopped. Nothing will run until you lift this.
            </p>
            <button
              onClick={() => api.releaseLockdown().then(() => setLockdown(false))}
              className="mt-2 text-[11px] text-slate-300 hover:text-white"
            >
              lift the lockdown
            </button>
          </div>
        )}

        <form onSubmit={submit} className="border-t border-ink-800 px-6 py-4">
          <div className="flex items-end gap-2">
            <input
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder={connected ? "Say what you need…" : "reconnecting…"}
              disabled={!connected}
              className="flex-1 rounded-xl bg-ink-900 px-4 py-2.5 text-sm text-slate-100 outline-none
                         placeholder:text-slate-600 focus:ring-1 focus:ring-thursday/40
                         disabled:opacity-50"
            />
            {thinking ? (
              <button
                type="button"
                onClick={interrupt}
                className="rounded-xl bg-ink-800 px-4 py-2.5 text-sm text-slate-300 hover:bg-ink-700"
              >
                stop
              </button>
            ) : (
              <button
                type="submit"
                disabled={!connected || draft.trim() === ""}
                className="rounded-xl bg-thursday/80 px-4 py-2.5 text-sm text-white
                           hover:bg-thursday disabled:opacity-30"
              >
                send
              </button>
            )}
          </div>
          <p className="mt-1.5 text-[10px] text-slate-700">esc stops whatever is running</p>
        </form>
      </main>

      <aside className="flex w-[19rem] shrink-0 flex-col border-l border-ink-800">
        <nav className="flex items-center gap-1 border-b border-ink-800 px-2 py-2">
          {(Object.keys(DRAWERS) as Drawer[]).map((key) => (
            <button
              key={key}
              onClick={() => setDrawer(drawer === key ? null : key)}
              className={`rounded px-2 py-1 text-[11px] ${
                drawer === key ? "bg-ink-800 text-slate-200" : "text-slate-500 hover:text-slate-300"
              }`}
            >
              {DRAWERS[key].label}
            </button>
          ))}
          <button
            onClick={stopEverything}
            title="Stop every running task and revoke standing permissions"
            className="ml-auto rounded px-2 py-1 text-[11px] text-slate-600 hover:text-state-error"
          >
            stop all
          </button>
        </nav>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {drawer ? (
            DRAWERS[drawer].render()
          ) : (
            <p className="p-4 text-[11px] leading-relaxed text-slate-700">
              Nothing needs your attention. Open a drawer when you want to look — Thursday will
              speak up on its own if something is waiting.
            </p>
          )}
        </div>
      </aside>
    </div>
  );
}
