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
GET  /world          current world state snapshot                                §12
POST /emergency/stop {scope: all|agents|camera|microphone|devices|tokens}         §69
GET  /audit          ?from=&to=&actor=&tool=                                      §39
POST /undo/{action_id}                                                            §40
```

## 11.9 `WS /realtime`

Client→server: `turn`, `audio_chunk`, `interrupt`, `approve`, `context_update`
(active app/screen/selection), `gesture`, `ping`.
Server→client: `token` (streamed text), `voice_mode`, `state` (avatar state), `task_update`,
`agent_update`, `approval_request`, `notification`, `world_update`, `error`.

## 11.10 Conventions

- Idempotency-Key on all POSTs that act on the world.
- Every response carries `trace_id`; it matches the logs and the audit rows.
- Rate limits per user *and* per device.
- Long work never blocks the conversation: the response returns a `task_id` and streams
  progress over `/realtime` (§43).
