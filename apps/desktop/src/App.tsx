import { useEffect, useState } from "react";
import { AgentStrip } from "@/components/AgentStrip";
import { ApprovalDialog } from "@/components/ApprovalDialog";
import { BrainGraph } from "@/components/BrainGraph";
import { Conversation } from "@/components/Conversation";
import { DevicePanel } from "@/components/DevicePanel";
import { Hud } from "@/components/Hud";
import { MemoryPanel } from "@/components/MemoryPanel";
import { PermissionPanel } from "@/components/PermissionPanel";
import { ServerConnect } from "@/components/ServerConnect";
import { TaskPanel } from "@/components/TaskPanel";
import { useMind } from "@/hooks/useMind";
import { useRealtime } from "@/hooks/useRealtime";
import { api } from "@/lib/api";
import { IS_TAURI } from "@/lib/origin";

const DRAWERS = {
  tasks: { label: "tasks", render: () => <TaskPanel /> },
  devices: { label: "devices", render: () => <DevicePanel /> },
  memory: { label: "memory", render: () => <MemoryPanel /> },
  permissions: { label: "permissions", render: () => <PermissionPanel /> },
} as const;

type Drawer = keyof typeof DRAWERS;

/**
 * PART 63/64, redrawn in Sprint 81. One window, one identity — now full-screen.
 *
 * The conversation is still the whole interface; what changed is what it sits on. Behind it
 * is Thursday itself: a graph of every machine, job, task and question it currently has,
 * moving because those things are moving. The owner asked for something they could watch
 * and read at a glance, and the honest way to build that is to draw the state that already
 * exists rather than to add a decorative layer above it.
 *
 * Nothing here decides how Thursday feels. `expression` arrives derived from the server
 * (ADR 0054), which is what keeps this screen and the avatar window from drifting apart.
 */
export default function App() {
  const {
    connected,
    messages,
    expression,
    approvals,
    agents,
    thinking,
    needsSetup,
    send,
    interrupt,
    setApprovals,
  } = useRealtime();
  const live = useMind(agents, approvals);
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

  // Escape interrupts, and closes a drawer first. Not a nicety: PART 98 says stopping must
  // never be buried in a menu.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (drawer) setDrawer(null);
      else interrupt();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [interrupt, drawer]);

  // A phone has no local Thursday to fall back to (ADR 0057) — several failed connection
  // attempts with nothing configured, or a stored address gone quiet, means asking rather
  // than leaving the owner looking at a HUD that will never say anything. `IS_TAURI`
  // guards it for the same reason `origin.ts` only reads the override under Tauri: a plain
  // browser dev session has its own proxy and a different, unrelated reason to be
  // unreachable.
  if (IS_TAURI && needsSetup) {
    return <ServerConnect />;
  }

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
    <div className="relative h-screen w-screen overflow-hidden bg-ink-void text-slate-200">
      <BrainGraph live={live} expression={expression} connected={connected} />
      <Hud expression={expression} devices={live.devices} connected={connected} />

      {/* The conversation floats over the graph rather than beside it: PART 64's rule is
          that the conversation is the interface, and putting it in a column would make the
          picture the subject and the talking a sidebar. */}
      <div className="absolute inset-x-0 bottom-0 flex justify-center">
        <div className="w-full max-w-3xl px-6 pb-6">
          <Conversation messages={messages} thinking={thinking} />

          <AgentStrip agents={agents} />

          {approvals.map((approval) => (
            <ApprovalDialog
              key={approval.id}
              approval={approval}
              onResolved={(id) => setApprovals((prior) => prior.filter((a) => a.id !== id))}
            />
          ))}

          {lockdown && (
            <div className="mb-3 rounded-xl border border-state-error/50 bg-state-error/10 p-3">
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

          <form onSubmit={submit} className="mt-2">
            <div className="flex items-end gap-2">
              <input
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                placeholder={connected ? "Say what you need…" : "reconnecting…"}
                disabled={!connected}
                className="flex-1 rounded-xl border border-white/10 bg-ink-900/70 px-4 py-3 text-sm
                           text-slate-100 outline-none backdrop-blur-md
                           placeholder:text-slate-600 focus:border-thursday/40
                           focus:ring-1 focus:ring-thursday/30 disabled:opacity-50"
              />
              {thinking ? (
                <button
                  type="button"
                  onClick={interrupt}
                  className="rounded-xl border border-white/10 bg-ink-800/80 px-4 py-3 text-sm
                             text-slate-300 backdrop-blur-md hover:bg-ink-700"
                >
                  stop
                </button>
              ) : (
                <button
                  type="submit"
                  disabled={!connected || draft.trim() === ""}
                  className="rounded-xl bg-thursday/80 px-4 py-3 text-sm text-white
                             hover:bg-thursday disabled:opacity-30"
                >
                  send
                </button>
              )}
            </div>
            <p className="mt-1.5 text-center text-[10px] text-slate-700">
              esc stops whatever is running
            </p>
          </form>
        </div>
      </div>

      {/* The drawers stay drawers. They open over the HUD and close on escape, because
          PART 64's rule is that none of them should have to be open for Thursday to make
          sense. */}
      <nav className="absolute bottom-6 left-8 flex items-center gap-1">
        {(Object.keys(DRAWERS) as Drawer[]).map((key) => (
          <button
            key={key}
            onClick={() => setDrawer(drawer === key ? null : key)}
            className={`rounded px-2 py-1 text-[11px] transition-colors ${
              drawer === key ? "bg-white/10 text-slate-200" : "text-slate-600 hover:text-slate-300"
            }`}
          >
            {DRAWERS[key].label}
          </button>
        ))}
        <button
          onClick={stopEverything}
          title="Stop every running task and revoke standing permissions"
          className="ml-2 rounded px-2 py-1 text-[11px] text-slate-700 hover:text-state-error"
        >
          stop all
        </button>
      </nav>

      {drawer && (
        <aside
          className="absolute right-0 top-0 flex h-full w-[21rem] flex-col border-l border-white/10
                     bg-ink-950/85 backdrop-blur-xl"
        >
          <div className="flex items-center border-b border-white/10 px-4 py-3">
            <span className="text-[11px] uppercase tracking-[0.2em] text-slate-400">
              {DRAWERS[drawer].label}
            </span>
            <button
              onClick={() => setDrawer(null)}
              className="ml-auto text-[11px] text-slate-600 hover:text-slate-300"
            >
              close
            </button>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">{DRAWERS[drawer].render()}</div>
        </aside>
      )}
    </div>
  );
}
