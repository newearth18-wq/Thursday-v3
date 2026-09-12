import { useCallback, useEffect, useState } from "react";
import {
  NO_STANDING_ON_PHONE,
  PHONE_DEVICE_ACTIONS,
  machineFor,
  mayGrantStanding,
  refusalFor,
} from "@/lib/surface";
import { api } from "@/lib/api";
import type { Approval, Device, Task } from "@/lib/types";

/**
 * §64 — the phone: see what is happening, answer what is being asked.
 *
 * The Android build has shipped this frontend since Sprint 87 and had no layout for a phone,
 * so the owner got a desktop window at 400px. This is that layout, and it is deliberately two
 * things rather than seven: **what is happening** and **what needs an answer**. The phone is a
 * remote and an approval surface, not a second desk (`apps/mobile/README.md`), and the way a
 * phone interface goes wrong is by trying to be the whole app in a column.
 *
 * Three properties are not about layout at all:
 *
 * **No standing permission.** "Always allow" is never offered here, whatever the engine said —
 * see `lib/surface.ts` for why, and the sentence saying so is shown rather than left to be
 * noticed.
 *
 * **The consequence is never shortened.** What happens, and what happens if the owner says no,
 * are shown in full and wrap. A truncated consequence is the version the owner would actually
 * read, which makes truncation a decision about what they get to know.
 *
 * **The machine is always named.** At a desk the owner is at the machine; here they are not,
 * and "delete these files" without "on Office-PC" is a different question.
 */
export function Phone({ connected }: { connected: boolean }) {
  const [devices, setDevices] = useState<Device[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [d, t, a] = await Promise.all([api.devices(), api.tasks(), api.approvals()]);
      setDevices(d.devices);
      setTasks(t.tasks);
      setApprovals(a.approvals);
      setError(null);
    } catch (e) {
      setError(String(e).replace(/^Error: \d+ [^:]+: /, ""));
    }
  }, []);

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 5000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const running = tasks.filter((task) => !["COMPLETED", "FAILED", "CANCELLED"].includes(task.status));

  return (
    <div className="flex min-h-screen flex-col gap-4 bg-ink-950 px-4 py-5 text-slate-300">
      <header className="flex items-baseline gap-2">
        <h1 className="text-sm text-slate-200">Thursday</h1>
        <span className={`text-[11px] ${connected ? "text-state-speaking" : "text-state-warning"}`}>
          {connected ? "เชื่อมต่ออยู่" : "ยังไม่ได้เชื่อมต่อ"}
        </span>
      </header>

      {/* Approvals first. They are the only thing on this screen that is waiting on the
          owner, and a phone that buries them under status has missed the point of being
          reachable. */}
      {approvals.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-[11px] uppercase tracking-wider text-state-warning">
            รออนุมัติ {approvals.length}
          </h2>
          {approvals.map((approval) => (
            <PhoneApproval key={approval.id} approval={approval} onResolved={refresh} />
          ))}
        </section>
      )}

      <section>
        <h2 className="mb-1 text-[11px] uppercase tracking-wider text-slate-500">เครื่อง</h2>
        {devices.length === 0 && <p className="text-[11px] text-slate-600">ยังไม่มีเครื่องที่จับคู่ไว้</p>}
        <ul data-testid="devices" className="space-y-1">
          {devices.map((device) => (
            <li key={device.id} className="flex items-center gap-2 rounded bg-ink-900 px-3 py-2">
              <span
                aria-hidden
                className={`h-2 w-2 shrink-0 rounded-full ${
                  device.status === "online"
                    ? "bg-state-speaking"
                    : device.status === "sleeping"
                      ? "bg-slate-500"
                      : "bg-slate-700"
                }`}
              />
              <span className="truncate text-xs text-slate-200">{device.name}</span>
              <span className="ml-auto shrink-0 text-[11px] text-slate-500">
                {device.status === "online"
                  ? "เปิดอยู่"
                  : device.status === "sleeping"
                    ? "หลับอยู่"
                    : "ปิดอยู่"}
              </span>
            </li>
          ))}
        </ul>
        {devices.map((device) => (
          <DeviceControls key={`${device.id}-controls`} device={device} onActed={setNote} />
        ))}
      </section>

      <section>
        <h2 className="mb-1 text-[11px] uppercase tracking-wider text-slate-500">กำลังทำ</h2>
        {running.length === 0 && <p className="text-[11px] text-slate-600">ตอนนี้ไม่มีงานค้างอยู่</p>}
        <ul className="space-y-1">
          {running.map((task) => (
            <li key={task.id} className="rounded bg-ink-900 px-3 py-2">
              <div className="flex items-center gap-2">
                <span className="truncate text-xs text-slate-200">{task.title}</span>
                <span className="ml-auto shrink-0 text-[11px] text-slate-500">
                  {Math.round(task.progress * 100)}%
                </span>
              </div>
              <div className="mt-1.5 h-1 rounded bg-ink-950">
                <div
                  className="h-1 rounded bg-thursday"
                  style={{ width: `${Math.round(task.progress * 100)}%` }}
                />
              </div>
            </li>
          ))}
        </ul>
      </section>

      {note && <p className="text-[11px] text-state-speaking">{note}</p>}
      {error && <p className="text-[11px] text-state-warning">{error}</p>}
    </div>
  );
}

/**
 * What a phone may do *to* a machine: lock it, wake it, and nothing that cannot be taken
 * back from where the owner is standing. `system.power` is shown and refused with the
 * reason rather than left off — a control that is silently absent teaches nothing.
 *
 * A gated action does not run here. It comes back as an approval, which appears at the top
 * of this same screen for the owner to answer once.
 */
function DeviceControls({
  device,
  onActed,
}: {
  device: Device;
  onActed: (note: string) => void;
}) {
  const [busy, setBusy] = useState("");
  const refused = refusalFor("system.power", "phone");

  const run = async (action: string, label: string) => {
    setBusy(action);
    try {
      const outcome = await api.deviceAction(device.id, action, {}, label);
      onActed(
        outcome.approval_id
          ? `${label} ${device.name}: รออนุมัติอยู่ด้านบน`
          : `${label} ${device.name}: เรียบร้อย`,
      );
    } catch (e) {
      onActed(`${label} ${device.name}: ${String(e).replace(/^Error: \d+ [^:]+: /, "")}`);
    } finally {
      setBusy("");
    }
  };

  return (
    <div className="mt-1 flex flex-wrap items-center gap-2 px-1">
      <span className="text-[10px] text-slate-600">{device.name}</span>
      {PHONE_DEVICE_ACTIONS.map((action) => (
        <button
          key={action}
          disabled={busy !== ""}
          onClick={() => run(action, action === "system.lock" ? "ล็อกหน้าจอ" : "ปลุกเครื่อง")}
          className="rounded bg-ink-900 px-2 py-1.5 text-[11px] text-slate-400 disabled:opacity-50"
        >
          {action === "system.lock" ? "ล็อกหน้าจอ" : "ปลุกเครื่อง"}
        </button>
      ))}
      <button
        disabled
        title={refused}
        className="cursor-not-allowed rounded bg-ink-900 px-2 py-1.5 text-[11px] text-slate-700 line-through"
      >
        ปิดเครื่อง
      </button>
      <p className="w-full text-[10px] text-slate-600">{refused}</p>
    </div>
  );
}

function PhoneApproval({
  approval,
  onResolved,
}: {
  approval: Approval;
  onResolved: () => void;
}) {
  const [busy, setBusy] = useState(false);
  // Always false here, and read from the shared rule rather than hardcoded, so the desktop
  // and the phone cannot drift into disagreeing about what a surface may offer.
  const standing = mayGrantStanding(approval.scopes_offered, "phone");

  const decide = async (approve: boolean) => {
    setBusy(true);
    try {
      if (approve) await api.approve(approval.id, "once");
      else await api.reject(approval.id);
      onResolved();
    } finally {
      setBusy(false);
    }
  };

  return (
    <article className="rounded-xl border border-state-warning/40 bg-state-warning/5 p-3">
      <div className="mb-2 flex flex-wrap items-center gap-1.5">
        <span className="rounded bg-ink-800 px-1.5 py-0.5 text-[10px] text-slate-400">
          ความเสี่ยง {approval.risk.toLowerCase()}
        </span>
        {!approval.reversible && (
          <span className="rounded bg-state-error/20 px-1.5 py-0.5 text-[10px] text-state-error">
            ย้อนกลับไม่ได้
          </span>
        )}
      </div>

      <p className="break-words font-mono text-xs text-slate-200">{approval.action}</p>
      {approval.resource && (
        <p className="break-words font-mono text-[11px] text-slate-400">{approval.resource}</p>
      )}

      {/* Which machine, always. The owner is not at it. */}
      <p className="mt-1 text-[11px] text-slate-400">
        บนเครื่อง <span className="text-slate-200">{machineFor(approval.device_name)}</span>
      </p>

      {/* In full, wrapping. A shortened consequence is the version that gets read. */}
      <dl className="mt-2 space-y-1 text-[11px]">
        <dt className="text-slate-500">จะเกิดอะไรขึ้น</dt>
        <dd className="whitespace-pre-wrap break-words text-slate-300">
          {approval.expected_outcome}
        </dd>
        <dt className="text-slate-500">ถ้าปฏิเสธ</dt>
        <dd className="whitespace-pre-wrap break-words text-slate-400">
          {approval.consequence_of_refusal}
        </dd>
      </dl>

      <div className="mt-3 flex gap-2">
        <button
          disabled={busy}
          onClick={() => decide(true)}
          className="flex-1 rounded-lg bg-state-speaking/20 px-3 py-2.5 text-xs font-medium
                     text-state-speaking disabled:opacity-50"
        >
          อนุมัติครั้งนี้
        </button>
        <button
          disabled={busy}
          onClick={() => decide(false)}
          className="flex-1 rounded-lg bg-ink-800 px-3 py-2.5 text-xs text-slate-400 disabled:opacity-50"
        >
          ปฏิเสธ
        </button>
      </div>

      {!standing && <p className="mt-1.5 text-[10px] text-slate-600">{NO_STANDING_ON_PHONE}</p>}
    </article>
  );
}
