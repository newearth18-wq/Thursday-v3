import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Device } from "@/lib/types";

/** PART 68. What Thursday can reach, and what each machine will let it do. */
export function DevicePanel() {
  const [devices, setDevices] = useState<Device[]>([]);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    const refresh = () => api.devices().then((r) => setDevices(r.devices)).catch(() => undefined);
    refresh();
    const timer = setInterval(refresh, 5000);
    return () => clearInterval(timer);
  }, []);

  return (
    <div className="space-y-2 p-4">
      {devices.length === 0 && <p className="text-xs text-slate-600">no devices connected</p>}
      {devices.map((device) => {
        const granted = device.capabilities?.granted ?? [];
        return (
          <div key={device.id} className="rounded-lg bg-ink-900 p-2.5">
            <button
              onClick={() => setOpen(open === device.id ? null : device.id)}
              className="flex w-full items-center gap-2 text-left"
            >
              <span
                className={`h-1.5 w-1.5 rounded-full ${
                  device.status === "online" ? "bg-state-speaking" : "bg-state-idle"
                }`}
              />
              <span className="text-xs text-slate-300">{device.name}</span>
              <span className="text-[10px] text-slate-600">{device.os}</span>
              <span className="ml-auto text-[10px] text-slate-600">{granted.length} caps</span>
            </button>

            {open === device.id && (
              <div className="mt-2 flex flex-wrap gap-1">
                {granted.map((capability) => (
                  <span key={capability}
                        className="rounded bg-ink-800 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">
                    {capability}
                  </span>
                ))}
                {device.status !== "online" && device.last_seen_at && (
                  <p className="mt-1 w-full text-[10px] text-slate-600">
                    last seen {new Date(device.last_seen_at).toLocaleString()}
                  </p>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
