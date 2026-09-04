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
  /**
   * Internal, and never rendered. It is the key world state uses (see
   * `WorldStateProjector.on_agent`), so it is what tells two concurrent jobs apart —
   * but Sprint 65's rule is that a class name never reaches a screen, and `activity`
   * is the field that does.
   */
  name: string;
  /** The allowlisted phrase from `plain.activity`. This is what a person reads. */
  activity: string;
  state: "working" | "completed" | "failed" | "waiting";
}

/** Sprint 80. Thursday's own condition — never a reading of the person (§55). */
export type Mood =
  | "STOPPED" | "FAILING" | "CONCERNED" | "WAITING"
  | "UNSURE" | "WORKING" | "PLEASED" | "ATTENTIVE" | "CALM";

/**
 * What Thursday is doing and how it is going, derived on the server.
 *
 * The client never computes a mood of its own. One derivation means the HUD and the
 * avatar cannot disagree about how Thursday feels, and it means the rule that a mood
 * cannot be asserted holds on this side of the socket too — there is nothing here to set.
 */
export interface Expression {
  mood: Mood;
  /** "" when nothing is running. Never an agent name. */
  activity: string;
  because: string;
  /** 0–1. How much motion to draw with. */
  intensity: number;
  running: number;
  waiting: number;
  unhealthy: number;
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
