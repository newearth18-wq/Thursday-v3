/** Mirrors the server's contracts. Kept narrow on purpose: the UI needs what it renders. */

export type VoiceMode = "NORMAL" | "THINKING" | "SUCCESS" | "WARNING" | "URGENT" | "QUIET";

/** PART 65. One animation, driven by one field. */
export type AvatarState =
  | "IDLE" | "LISTENING" | "THINKING" | "WORKING"
  | "SPEAKING" | "WAITING_APPROVAL" | "WARNING" | "ERROR";

export type TaskStatus =
  | "NEW" | "PLANNING" | "READY" | "RUNNING" | "WAITING" | "WAITING_APPROVAL"
  | "BLOCKED" | "PAUSED" | "VERIFYING" | "COMPLETED" | "FAILED" | "CANCELLED";

export interface Message {
  id: string;
  role: "owner" | "thursday";
  text: string;
  voiceMode?: VoiceMode;
  /** False when an action was dispatched but its effect could not be observed (PART 5.1). */
  verified?: boolean;
  confidence?: number;
  detail?: string | null;
  at: string;
}

export interface Approval {
  id: string;
  action: string;
  agent?: string | null;
  device_name?: string | null;
  resource: string;
  risk: string;
  reversible: boolean;
  expected_outcome: string;
  consequence_of_refusal: string;
  /** ASK_ALWAYS approvals offer only ONCE — the UI must not invent "always" (ADR 0008). */
  scopes_offered?: string[];
  policy?: string;
}

export interface Task {
  id: string;
  title: string;
  status: TaskStatus;
  progress: number;
  objective?: string;
}

export interface Device {
  id: string;
  name: string;
  kind: string;
  os: string;
  status: "online" | "offline" | "sleeping";
  capabilities: { granted: string[] };
  last_seen_at?: string | null;
  telemetry?: Record<string, unknown> | null;
}

export interface MemoryRecord {
  id: string;
  layer: string;
  content: string;
  confidence: number;
  importance: number;
  source: string;
  pinned: boolean;
  score?: number | null;
}

export interface AgentStatus {
  name: string;
  state: "working" | "completed" | "failed" | "waiting";
}

export interface RealtimeMessage {
  type: string;
  [key: string]: unknown;
}

/** PART 70. One row of the policy table, already resolved at the current autonomy level. */
export interface Policy {
  action: string;
  namespace: string;
  decision: "AUTO" | "ASK_ONCE" | "ASK_ALWAYS" | "BLOCK";
  level: string;
  risk: string;
  reversible: boolean;
  requires_backup: boolean;
  bulk_threshold: number | null;
  blocked: boolean;
  /** False when the table would ignore a relaxation, so the control is not offered. */
  can_relax: boolean;
}

export interface Grant {
  id: string;
  action: string;
  resource_glob: string;
  expires_at?: string | null;
  uses_remaining?: number | null;
}

export interface PendingMemory {
  index: number;
  content: string;
  layer: string;
  source?: string;
  proposed_by?: string | null;
}
