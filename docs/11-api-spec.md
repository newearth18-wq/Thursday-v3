# 11. API Specification

Base `/api/v1`. Auth: bearer (user session) or device token. All responses are Pydantic
models; all errors share `{error: {code, message, details, trace_id}}`.

## 11.1 Conversation

```
POST /conversation                  → talk to Thursday
  {text?, audio_ref?, device_id, session_id?, context_hints?, stream?}
  ← {reply, voice_mode, task_id?, intent, confidence, sources[], approvals_required[]}

POST /conversation/{sid}/interrupt  → §44 "Thursday หยุด": stop TTS, pause agents,
                                       cancel unsafe in-flight actions, return state
GET  /conversation/{sid}            → windowed history
```

## 11.2 Tasks (§41–43)

```
POST   /tasks                  {objective, project_id?, deadline?, budget?, device_id?}
GET    /tasks                  ?status=&project_id=&limit=
GET    /tasks/{id}             full object incl. plan, steps, verification
POST   /tasks/{id}/cancel
POST   /tasks/{id}/pause | /resume | /retry
GET    /tasks/{id}/events      SSE stream of step transitions
```

## 11.3 Devices (§21, §22)

```
GET  /devices                          list + status + capabilities
GET  /devices/{id}
POST /devices/{id}/action              {action, args, reason}  → permission-checked
POST /devices/enroll                   {pairing_code, public_key, name, os, capabilities}
POST /devices/{id}/revoke
WS   /device                           node protocol (TNP/1, §9)
```

## 11.4 Agents

```
GET  /agents                    registry + capabilities + health
GET  /agents/runs?task_id=
POST /agents/dynamic            create a temporary agent from a spec (§16)
```

## 11.5 Memory (§7)

```
GET    /memory/search   ?q=&layer=&project_id=&k=&min_confidence=
POST   /memory          {layer, content, structured?, importance, source, project_id?}
PATCH  /memory/{id}     supersede / repin / adjust importance
DELETE /memory/{id}
GET    /memory/conflicts        pending contradictions awaiting a decision
POST   /memory/conflicts/{id}   {resolution}
GET    /memory/timeline ?from=&to=          §56
GET    /graph/query     ?entity=&hops=      §10
```

## 11.6 Approvals (§38)

```
GET  /approvals                       pending, newest first
POST /approvals/{id}   {decision: approve|reject|approve_once|always_allow, scope?, note?}
```

## 11.7 Skills / Automations / Projects

```
GET|POST /skills            GET /skills/{id}/versions   POST /skills/{id}/test (sandbox)
POST /skills/{id}/activate  POST /skills/{id}/rollback  {version}
GET|POST /automations       POST /automations/{id}/enable|disable|run
GET|POST /projects          GET /projects/{id}/brain    §54
POST /decisions             §55 decision journal entry
```

## 11.8 System

```
GET  /health         db, redis, models, devices, agents, queue, disk, cpu, ram  §59
GET  /health/{database|redis|devices|models}   one dependency, for a probe      PART 91
GET  /world          current world state snapshot                                §12
POST /emergency/stop {scope: all|agents|camera|microphone|devices|tokens}         §69
POST /emergency/release                                                           §69
GET  /audit          ?from=&to=&actor=&tool=                                      §39
POST /undo/{action_id}                                                            §40
GET  /agents         registered agents with their measured success rate
GET  /tools          the tool catalogue, with dry-run and undo support flags
GET  /autonomy       {autonomy, proactivity} — acting and speaking, separately  PART 97
POST /autonomy       ?autonomy=MODERATE&proactivity=LOW
GET  /policies       every action, its effective decision, and whether it can   PART 70
                     be relaxed at all
POST /policies/{action}?decision=ASK_ALWAYS
```

`GET /autonomy` reports level *names*, and `POST /autonomy` accepts them (as well as the
numbers behind them), so a value this API hands out is a value it takes back.

`GET /policies` reports the decision **after** the current autonomy level is applied, not
the shipped default — a panel that shows one while the other is in force is lying to the
owner. `can_relax` says in advance whether a change to `AUTO` would take effect;
`POST /policies/{action}` refuses a change the table would silently ignore (400) rather
than accepting a setting that then reverts, and refuses anything hard-blocked outright.

## 11.9 `WS /realtime`

Client→server: `turn`, `audio_chunk`, `interrupt`, `approve`, `context_update`
(active app/screen/selection), `gesture`, `ping`.
Server→client (PART 72's vocabulary): `ready`, `assistant.delta`, `assistant.audio`,
`task.updated`, `agent.updated`, `approval.required`, `approval.resolved`,
`device.updated`, `notification`, `error`. Internal event kinds are translated at the
gateway, so a rename inside the core is not a breaking change to every client.

## 11.10 Conventions

- Idempotency-Key on all POSTs that act on the world.
- Every response carries `trace_id`; it matches the logs and the audit rows.
- Rate limits per user *and* per device.
- Long work never blocks the conversation: the response returns a `task_id` and streams
  progress over `/realtime` (§43).
