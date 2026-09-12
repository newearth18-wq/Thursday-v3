/** The REST surface, typed. Every call goes through here so the base URL lives in one place. */

import { API_ORIGIN } from "./origin";
import type { Approval, Device, MemoryRecord, Policy, Task } from "./types";
import type { Consequence, Graph, Problem } from "./workflow";

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

  // V15. `preview` is called on every edit and writes nothing; everything else is a
  // deliberate act by the owner, and saving is not one of the acts that arms a rule.
  automations: () => request<{ automations: StoredWorkflow[] }>("/automations"),
  automationCatalogue: () => request<Catalogue>("/automations/catalogue"),
  previewWorkflow: (graph: Graph) =>
    request<WorkflowReport>("/automations/preview", {
      method: "POST",
      body: JSON.stringify(graph),
    }),
  saveWorkflow: (graph: Graph, id?: string) =>
    request<WorkflowReport & { id: string; enabled: boolean }>(
      id ? `/automations/${id}` : "/automations",
      { method: id ? "PUT" : "POST", body: JSON.stringify(graph) },
    ),
  enableWorkflow: (id: string, enabled: boolean) =>
    request<{ id: string; enabled: boolean }>(
      `/automations/${id}/enable?enabled=${enabled}`,
      { method: "POST" },
    ),
  runWorkflow: (id: string) =>
    request<{ ran: boolean; steps: number }>(`/automations/${id}/run`, { method: "POST" }),
  deleteWorkflow: (id: string) => request(`/automations/${id}`, { method: "DELETE" }),
  learn: () => request<LearningCentre>("/learn"),
  startLesson: (id: string) =>
    request<LessonStep>(`/learn/${encodeURIComponent(id)}/start`, { method: "POST" }),
  // The body is evidence of what happened, not a claim that it did.
  attemptLesson: (id: string, evidence: unknown) =>
    request<LessonStep>(`/learn/${encodeURIComponent(id)}/attempt`, {
      method: "POST",
      body: JSON.stringify(evidence ?? null),
    }),
  skipLesson: (id: string) =>
    request<LessonStep>(`/learn/${encodeURIComponent(id)}/skip`, { method: "POST" }),

  describeCron: (cron: string) =>
    request<{ reads_as: string; valid: boolean; problem?: string }>(
      `/automations/schedule/describe?cron=${encodeURIComponent(cron)}`,
    ),
};

export interface WorkflowReport {
  valid: boolean;
  problems: Problem[];
  consequences: Consequence[];
  explanation: string;
}

export interface StoredWorkflow extends Graph {
  automation_id: string | null;
  enabled: boolean;
  created_by: string;
  run_count: number;
  last_run_at: string | null;
  explanation: string;
}

// V16 — §10's "เรียนรู้ Thursday". `attempt` takes evidence, never a verdict: there is no
// field here through which this client could say a step succeeded, because the step's own
// check reads the machine and decides (ADR 0012).
export interface Lesson {
  id: string;
  name: string;
  minutes: number;
  done: boolean;
  available: boolean;
  reason?: string;
}

export interface LearningStage {
  stage: string;
  title: string;
  lessons: Lesson[];
}

export interface LessonStep {
  lesson: string;
  step: string;
  passed: boolean;
  message: string;
  done: boolean;
  next: { show: string; try: string };
}

export interface PracticeOffer {
  practice: boolean;
  action: string;
  would: string;
  decision: string;
  why: string;
  risk: string;
  reversible: boolean;
}

export interface LearningCentre {
  summary: string;
  areas: { area: string; title: string; features: string[]; example: string }[];
  path: LearningStage[];
  progress: { verbosity: string; teaching: string; used: string[]; tutorials_completed: string[] };
  next: { id: string; name: string; stage_title: string; minutes: number; reason: string } | null;
  practice: PracticeOffer[];
}

export interface Catalogue {
  triggers: { kind: string; unavailable: string }[];
  conditions: string[];
  actions: { kind: string; needs: string; unavailable: string }[];
  tools: { name: string; level: string; decision: string; blocked: boolean }[];
}
