# 4. Database Schema

PostgreSQL 16 + pgvector. Every table has `id uuid pk`, `created_at`, `updated_at`.
Migrations via Alembic — no hand-created schema, ever (§81).

## 4.1 Entity map

```
users ─┬─< devices ─< device_sessions
       ├─< projects ─< tasks ─< task_steps ─< tool_runs
       │                  │                ─< agent_runs
       │                  └─< approvals
       ├─< goals ─< missions ─< projects
       ├─< memories ─< memory_links        (+ memory_conflicts)
       ├─< documents (Obsidian/file index)
       ├─< entities ─< relationships       (knowledge graph)
       ├─< skills ─< skill_versions ─< skill_runs
       ├─< automations ─< automation_runs
       ├─< events
       ├─< notifications
       ├─< permissions / permission_grants
       ├─< audit_logs
       ├─< observations (vision/spatial)
       └── world_state (1 row per user)
```

## 4.2 Core tables

### users
`id, email, display_name, locale, timezone, proactivity_level, voice_profile jsonb, settings jsonb`

### devices  (§21)
```
id, user_id, name, kind(desktop|laptop|phone|tablet|server|speaker|glasses),
os, os_version, node_version, fingerprint, public_key,
status(online|offline|sleeping), last_seen_at,
capabilities jsonb,        -- {"open_app":true,"screenshot":true,"camera":false,...}
telemetry  jsonb,          -- battery, network, active_app, active_user, screen_locked
trust_level(int), enrolled_at, revoked_at
```
`capabilities` is the Capability Registry (§57) row; the Device Router queries it.

### tasks  (§41, §42)
```
id, user_id, project_id, parent_task_id, goal_id,
title, objective, status(enum), priority(int), progress(float),
plan jsonb,                -- the step DAG as planned
assigned_agent, origin_device_id, target_device_id,
budget jsonb,              -- {tokens, usd, seconds, agent_calls, tool_calls}
spent  jsonb,
deadline, started_at, finished_at,
result jsonb, error jsonb, verification jsonb,
trace_id
```
Status enum: `NEW PLANNING RUNNING WAITING WAITING_APPROVAL BLOCKED VERIFYING COMPLETED FAILED CANCELLED`.

### task_steps
`id, task_id, seq, kind(tool|agent|device|ask_user), name, contract jsonb, depends_on uuid[], status, attempt, max_attempts, input jsonb, output jsonb, started_at, finished_at`

### agent_runs / tool_runs  (§39, §82)
```
agent_runs: id, task_id, step_id, agent_name, agent_version, contract jsonb,
            input_summary, output jsonb, verdict(pass|retry|escalate|na),
            tokens_in, tokens_out, cost_usd, duration_ms, model, trace_id, error
tool_runs : id, task_id, step_id, tool_name, device_id, args_summary jsonb,
            result_summary jsonb, ok bool, risk, permission_decision,
            approval_id, duration_ms, undo jsonb, trace_id, error
```
`args_summary`/`result_summary` are **redacted** projections — raw payloads that may hold
secrets are never persisted (§35).

### approvals  (§38)
`id, user_id, task_id, step_id, action, agent, device_id, resource, risk, expected_outcome, rationale, state(pending|approved|rejected|expired), scope(once|session|always), decided_at, expires_at`

### memories  (§7, §11)
```
id, user_id, layer(working|episodic|semantic|preference|procedural|project),
project_id, key, content text, structured jsonb,
importance float, confidence float, relevance_decay float,
source(user|file|email|web|agent|camera|sensor|inference), source_ref,
embedding vector(768),
supersedes_id, superseded_by_id, valid_from, valid_to,
access_count, last_accessed_at, pinned bool, expires_at
```
Indexes: `hnsw(embedding vector_cosine_ops)`, `(user_id, layer)`, `(project_id)`, GIN on `structured`.

### memory_conflicts (§11)
`id, memory_id, incoming_content, old_value jsonb, new_value jsonb, old_source, new_source, old_confidence, new_confidence, detected_at, resolution(pending|kept_old|kept_new|both_valid|user_decided), resolved_by`

Conflicts are **rows**, not a merge heuristic. Thursday reports both values with sources
and timestamps rather than silently averaging them.

### entities / relationships  (§10 knowledge graph)
```
entities:      id, user_id, kind(person|project|task|document|event|device|decision|
                                 location|skill|object|organization), name, aliases text[],
               attributes jsonb, embedding vector(768)
relationships: id, user_id, src_entity_id, dst_entity_id, kind, weight, attributes jsonb,
               observed_at, source, valid_from, valid_to
```
Answering *"which file did I use in the last meeting with this person?"* is a 2-hop
traversal (`person –attended→ meeting –used→ document`) fused with memory + file index.

### world_state  (§12) — one row per user, updated in place, history in `events`
`user_id pk, owner_status, active_device_id, active_app, active_project_id, active_task_id, online_devices jsonb, running_agents jsonb, pending_approvals jsonb, location_context, open_files jsonb, recent_actions jsonb, updated_at`

### skills (§50–53)
`skills: id, user_id, name, slug, description, status(draft|testing|active|deprecated), current_version, owner, risk, tags text[]`
`skill_versions: id, skill_id, version, steps jsonb, tools text[], permissions jsonb, input_schema jsonb, output_schema jsonb, tests jsonb, changelog, approved_by, approved_at`
`skill_runs: id, skill_version_id, task_id, sandbox bool, ok, output jsonb, duration_ms`

### automations (§48) / events (§47)
`automations: id, user_id, name, trigger jsonb, conditions jsonb, actions jsonb, enabled, proactivity_min, last_run_at, run_count, created_by(user|thursday_suggested)`
`events: id, user_id, kind, source, device_id, payload jsonb, trace_id, occurred_at, processed_at`

### audit_logs  (§39) — append-only, no UPDATE/DELETE grant
`id, ts, user_id, actor(user|thursday|agent|automation|system), agent, task_id, device_id, tool, action, resource, input_summary, output_summary, result, permission_decision, approval_id, error, trace_id, prev_hash, hash`

`prev_hash/hash` chain the log so tampering is detectable.

### observations (§25, §26 spatial memory)
`id, user_id, device_id, object_label, confidence, bbox jsonb, location_context, frame_ref, seen_at, expires_at` — metadata only; **no video retained by default**.

### documents
`id, user_id, project_id, path, vault_rel_path, title, kind, hash, size, mtime, tags text[], summary, embedding vector(768), indexed_at`

## 4.3 Retention and hygiene

| Data | Default retention |
|---|---|
| `working` memories | 24 h or task end |
| conversation turns | 30 days rolling, then summarized into episodic memory |
| `observations` | 7 days |
| raw camera frames | **not stored** (opt-in, per-session only) |
| raw audio | **not stored** (opt-in) |
| `audit_logs` | 400 days, append-only |
| `tool_runs.args_summary` | redacted at write time |

## 4.4 Dev vs prod

The same SQLAlchemy models run on SQLite (dev/CI) with `Vector` degraded to JSON and a
brute-force cosine scan in `SqliteVectorStore`. This is why `MemoryProvider` and
`VectorProvider` are separate ports.
