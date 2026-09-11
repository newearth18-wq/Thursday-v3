import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { Live } from "@/lib/graph";
import type { AgentStatus, Approval, Device, Task } from "@/lib/types";

/** Statuses that mean a task is over. A finished task is not part of what Thursday is now. */
const DONE = new Set(["COMPLETED", "FAILED", "CANCELLED"]);

const EVERY_MS = 5000;

/**
 * What Thursday currently consists of.
 *
 * Two halves, arriving two ways, and the difference is not an accident: work and approvals
 * are pushed down the socket the instant they happen, because a graph that learns about a
 * running job five seconds late is a graph the owner stops believing. Devices and tasks are
 * polled, because nothing about them changes faster than a person can look.
 */
export function useMind(agents: AgentStatus[], approvals: Approval[]): Live {
  const [devices, setDevices] = useState<Device[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);

  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      const [d, t] = await Promise.allSettled([api.devices(), api.tasks()]);
      if (cancelled) return;
      // Settled rather than all: one endpoint being down should empty its own ring, not
      // blank the whole picture and leave the owner looking at an idle Thursday.
      setDevices(d.status === "fulfilled" ? d.value.devices : []);
      setTasks(t.status === "fulfilled" ? t.value.tasks : []);
    };
    void refresh();
    const timer = setInterval(refresh, EVERY_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  return useMemo(
    () => ({
      devices: devices.filter((device) => device.status !== "offline"),
      tasks: tasks.filter((task) => !DONE.has(task.status)),
      agents,
      approvals,
    }),
    [devices, tasks, agents, approvals],
  );
}
