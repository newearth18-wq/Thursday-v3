# 10. Event Architecture

## 10.1 Bus

One `EventBus` port. In-process `asyncio` fan-out for dev/tests; **Redis Streams** with
consumer groups in production (at-least-once, replayable, one stream per topic prefix).
Handlers must be idempotent by `event.id`.

```python
class Event(BaseModel):
    id: UUID; kind: str; source: str
    user_id: UUID; device_id: UUID | None; task_id: UUID | None
    payload: dict; trace_id: str; occurred_at: datetime
```

## 10.2 Topics (§79)

```
conversation.turn.received     task.created   task.started   task.step.completed
conversation.response.sent     task.waiting   task.approval_required
                               task.verifying task.completed task.failed task.cancelled
agent.started  agent.completed  agent.failed  agent.retry
tool.executed  tool.failed
device.connected device.disconnected device.telemetry device.event
memory.created memory.superseded memory.conflict
approval.required approval.granted approval.denied approval.expired
automation.triggered automation.completed
vision.object_seen vision.gesture
event.email_received event.calendar_event event.file_created event.file_changed
event.scheduled event.sensor
system.health system.lockdown
```

## 10.3 Consumers

| Consumer | Subscribes to | Does |
|---|---|---|
| `WorldStateProjector` | `device.*`, `task.*`, `agent.*`, `approval.*` | keeps `world_state` current |
| `AuditWriter` | `tool.*`, `approval.*`, `task.*`, `device.*` | append-only hash-chained log |
| `MemoryConsolidator` | `task.completed`, `conversation.*` | episodic/procedural writes, dedupe |
| `AutomationEngine` | everything | matches WHEN → runs DO/THEN |
| `NotificationRouter` | `task.*`, `approval.*`, `event.*` | dedupe, prioritize, pick device |
| `RoutineLearner` | `tool.executed` | mines repeated sequences, **suggests** (never auto-creates) |
| `HealthMonitor` | `system.health` | degradation + offline mode switching |
| `RealtimeGateway` | user-visible subset | pushes to WS clients |

## 10.4 Automation rules (§48)

```yaml
name: weekly-summary
when:  {type: schedule, cron: "0 16 * * FRI", timezone: Asia/Bangkok}
if:    [{owner_status: {not: dnd}}]
do:    [{task: summarize_week, budget: {usd: 0.20}}]
then:  [{obsidian_write: "08 Daily/{{date}} Weekly Review.md"},
        {notify: {priority: NORMAL, title: "สรุปงานประจำสัปดาห์พร้อมแล้ว"}}]
```
Triggers: `schedule | event | state_change | manual`. Every automation has a budget, a
proactivity floor, and an owner. Automations that would perform an L3+ action still hit the
Permission Engine at run time — automation is not an approval bypass.

## 10.5 Routine learning (§49)

The learner mines `tool_runs` for sequences repeated ≥4 times in ≥3 distinct days within a
2-hour daily band, then *proposes*: "ทุกเช้าคุณเปิด Browser, Calendar และ Drive —
ต้องการให้ผมสร้าง Morning Routine ไหม". **Thursday never creates an automation silently** (§49).

## 10.6 Proactivity gate (§46)

```
OFF    – never initiate
LOW    – only CRITICAL (deadline today, failure, security)
NORMAL – CRITICAL + IMPORTANT (conflicts, blocked tasks, finished long jobs)
HIGH   – + suggestions and optimizations
```
Plus rate limits (≤N/hour, quiet hours, none while a meeting is in progress), and
notification bundling (§67). Private notifications are never spoken aloud when the presence
signal says another person is present, unless explicitly permitted.
