/** The REST surface, typed. Every call goes through here so the base URL lives in one place. */

import { API_ORIGIN } from "./origin";
import type { Approval, Device, MemoryRecord, Policy, Task } from "./types";

const BASE = `${API_ORIGIN}/api/v1`;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`${response.status} ${path}: ${body.slice(0, 300)}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  tasks: () => request<{ tasks: Task[] }>("/tasks?limit=20"),
  cancelTask: (id: string) => request(`/tasks/${id}/cancel`, { method: "POST" }),
  pauseTask: (id: string) => request(`/tasks/${id}/pause`, { method: "POST" }),
  resumeTask: (id: string) => request(`/tasks/${id}/resume`, { method: "POST" }),

  devices: () => request<{ devices: Device[] }>("/devices"),

  approvals: () => request<{ approvals: Approval[] }>("/approvals"),
  approve: (id: string, scope = "once") =>
    request(`/approvals/${id}/approve?scope=${scope}`, { method: "POST" }),
  reject: (id: string) => request(`/approvals/${id}/reject`, { method: "POST" }),

  searchMemory: (q: string) =>
    request<{ memories: MemoryRecord[] }>("/memory/search", {
      method: "POST",
      body: JSON.stringify({ q, k: 20 }),
    }),
  forgetMemory: (id: string) => request(`/memory/${id}`, { method: "DELETE" }),
  memoryConflicts: () =>
    request<{ conflicts: { id: string; description: string; status: string }[] }>(
      "/memory/conflicts",
    ),
  resolveConflict: (id: string, resolution: string) =>
    request(`/memory/conflicts/${id}?resolution=${resolution}`, { method: "POST" }),
  memoryLinks: (id: string) =>
    request<{ links: { source_id: string; target_id: string; relation: string }[] }>(
      `/memory/links?memory_id=${id}`,
    ),
  writeMemory: (layer: string, content: string) =>
    request<{ written: boolean; decision?: string; reason?: string }>("/memory", {
      method: "POST",
      body: JSON.stringify({ layer, content, importance: 0.7 }),
    }),
  memoryConfirmations: () =>
    request<{ pending: { index: number; content: string; layer: string; proposed_by?: string }[] }>(
      "/memory/confirmations",
    ),
  confirmMemory: (index: number, accept: boolean) =>
    request("/memory/confirmations", { method: "POST", body: JSON.stringify({ index, accept }) }),

  policies: () => request<{ autonomy: string; policies: Policy[]; hard_blocked: string[] }>(
    "/policies",
  ),
  setPolicy: (action: string, decision: string) =>
    request<{ action: string; decision: string }>(
      `/policies/${encodeURIComponent(action)}?decision=${decision}`,
      { method: "POST" },
    ),
  grants: () => request<{ grants: { id: string; action: string; resource_glob: string }[] }>(
    "/approvals/grants",
  ),
  revokeGrant: (id: string) => request(`/approvals/grants/${id}`, { method: "DELETE" }),

  autonomy: () => request<{ autonomy: string; proactivity: string; note: string }>("/autonomy"),
  setAutonomy: (autonomy: string) =>
    request(`/autonomy?autonomy=${autonomy}`, { method: "POST" }),

  /** PART 69/98 — a plain call, deliberately not routed through the model. */
  emergencyStop: () =>
    request("/emergency/stop", { method: "POST", body: JSON.stringify({ scope: "all" }) }),
  releaseLockdown: () => request("/emergency/release", { method: "POST" }),

  health: () => request<{ ok: boolean; checks: { component: string; ok: boolean; detail: string }[] }>(
    "/health",
  ),
};
