# Thursday V2 — Architecture Review and Delta Plan

V2 locks decisions V1 left open and adds subsystems. This is the A–K response required by
PART 99, written against a codebase that already implements the V1 vertical slice.

---

## A. Architecture Review

### What V1 already satisfies

The V1 build meets V2's five non-negotiable principles, and meets them in code rather than
in prose:

| V2 principle | Where it lives |
|---|---|
| One Assistant (1.1) | the user never names an agent; `AgentRegistry.select` scores candidates |
| Modular (1.2) | every provider is a `Protocol`; concrete adapters are built once in the DI container |
| Local First (1.3) | rule-based LLM, hash embeddings, text STT/TTS stubs — a fresh checkout boots with zero infrastructure |
| Permission Before Power (1.4) | `PermissionEngine.decide()` on every action; hard-block set with no override path |
| Verify Before Success (1.5) | `tasks.complete()` raises on a non-passing verification; nodes report `verified` from an observation |

Those are the expensive properties to retrofit, and they are already load-bearing.

### What V2 changes, and why each change is real

V2 is not a restatement. Eleven differences change code:

1. **The stack is locked, not chosen.** V1 treated PostgreSQL/Redis/Dramatiq as the
   production target behind ports, with SQLite and an in-process bus as the running default.
   V2 makes them the default. → docker-compose, real adapters, in-process retained for tests.
2. **The repository tree is specified** (PART 3): `packages/`, `services/`, `database/`,
   `scripts/`, `docker/`. V1 used one flat distribution and argued the split could wait.
   V2 asks again; the split is cheapest now, at 17k lines. → see D.
3. **Two new task states**: `READY` and `PAUSED` (PART 5). `READY` separates *planned* from
   *started*, which is exactly what a queue needs in order to schedule. `PAUSED` is
   user-initiated and genuinely distinct from `WAITING` (a dependency) and `BLOCKED` (an
   obstacle) — conflating them loses the reason the task stopped.
4. **`ASK` splits into `ASK_ONCE` and `ASK_ALWAYS`** (PART 20). This is the difference
   between "remember this answer" and "never remember it". `file.delete` and `email.send`
   must be `ASK_ALWAYS`, so no standing grant can ever accumulate for them.
5. **Node commands are namespaced** (PART 23): `app.open`, `file.search`, `system.info`.
   The namespace is not cosmetic — the permission matrix and capability registry key on a
   prefix (`file.*`, `system.*`) instead of enumerating every verb.
6. **`ThursdayCore.handle_request(UserRequest) -> ThursdayResponse`** (PART 6). V1's
   `handle_turn(**kwargs)` grew one keyword at a time. A single input model is the right
   shape for multimodal input and for the API boundary.
7. **The intent taxonomy is specified** (PART 9) — 15 categories carrying
   `required_capabilities`, which is precisely what the Agent Router consumes.
8. **Autonomy levels 0–3** (PART 97). V1 had *proactivity* (when Thursday speaks).
   Autonomy is when Thursday *acts*. Different axes, different risks, both needed.
9. **Browser Agent on Playwright** (PART 31) — absent from V1.
10. **The memory decision is four-valued** (PART 39): STORE / TEMPORARY / IGNORE /
    ASK_USER. V1 returned a boolean. `ASK_USER` is the one that matters: a memory Thursday
    is unsure about should be confirmed, not guessed.
11. **Memory-poisoning defense is explicit** (PART 76): an agent may not write a user
    preference. V1 ranked sources by trust; V2 makes it a hard rule.

### What stays deliberately unbuilt

PART 105 is explicit: no camera, gesture, mobile or proactive features until the core path
passes automated tests. V1 built spatial memory and gesture *interpretation* ahead of that
line. They stay — tested and inert, with no capture path wired — and nothing further is
added there in this pass.

---

## B. Technical Decisions

Each is recorded as an ADR under `docs/architecture/decisions/`.

| ADR | Decision | Rationale |
|---|---|---|
| 0001 | Ports and adapters with a DI container | The only mechanism that makes "swap a provider" true rather than aspirational |
| 0002 | Postgres + pgvector as one datastore | A separate vector DB lets a memory and its metadata drift out of sync |
| 0003 | Dramatiq over Celery | Broker-agnostic, far fewer moving parts, no beat/result-backend ceremony for a single-user system |
| 0004 | In-house orchestrator; agent frameworks as adapters | The supervise/permission/verify loop is the product, not a framework's opinion |
| 0005 | Monorepo split into `packages/` | PART 3, done now while it is a mechanical rename |
| 0006 | SQLite + in-process bus retained for tests | Safety properties testable only against production are not tested |
| 0007 | Namespaced node commands | The permission matrix keys on prefixes; capability advertisement becomes a tree |
| 0008 | `ASK_ONCE` vs `ASK_ALWAYS` | Some actions must never accumulate a standing grant |
| 0009 | Autonomy separate from proactivity | Acting and speaking are different risks and deserve different dials |
| 0010 | Untrusted content is data, never instruction | Prompt-injection defense has to be structural, not a prompt line |

---

## C. MVP Boundary

**In — Core v1 (PART 101):** server runs · Postgres + Redis · desktop connects · node
registers · text command → task → agent routing → permission check → node opens Chrome →
verification → audit → correct task status → UI shows state → tests pass.

**In — Voice v1 (PART 102):** wake word · STT · TTS · barge-in, end to end.

**Out until the core path is green:** camera capture, gesture command dispatch, mobile,
proactive initiation, knowledge graph at scale, automatic dynamic-agent creation.

**Boundary rule:** a capability is in scope only if the vertical slice is *dishonest*
without it.

---

## D. Repository Tree

PART 3's tree, implemented as **one distribution over eleven source packages**. Each
`packages/<domain>/` holds `thursday_<domain>/`; the wheel lists all of them.

This matches the specified tree literally while keeping a single install, a single test run
and no cross-package version skew — which eleven separately versioned distributions would
introduce for a system that always deploys together. If a package ever needs to ship
independently, it already has its own directory and a clean import name; only its build
metadata would change.

```
thursday/
├── apps/
│   ├── desktop/            Tauri 2 + React + TS + Vite + Tailwind
│   ├── server/             uvicorn entrypoint
│   ├── node/               device node (Windows/macOS/Linux)
│   └── cli/                terminal client (embeds a core + a node)
├── packages/
│   ├── shared/thursday_shared/          types, ports, protocol, errors
│   ├── core/thursday_core/              engine, planner, routers, supervisor, world
│   ├── agents/thursday_agents/          registry, router, default agents, factory
│   ├── tools/thursday_tools/            tool registry + built-ins
│   ├── memory/thursday_memory/          layered memory, vectors, obsidian, graph
│   ├── voice/thursday_voice/            wake, VAD, STT, TTS, pipeline
│   ├── vision/thursday_vision/          spatial memory, gestures
│   ├── security/thursday_security/      permissions, policy, vault, privacy, audit
│   ├── automation/thursday_automation/  rules, engine, routines
│   ├── devices/thursday_devices/        node protocol, hub, node runtime
│   └── models/thursday_models/          LLM/embedding providers, model router
├── services/
│   ├── api/thursday_api/                FastAPI routers, schemas, deps
│   ├── worker/thursday_worker/          Dramatiq actors, background jobs
│   └── realtime/thursday_realtime/      WebSocket gateway
├── database/
│   ├── migrations/                      Alembic
│   └── seeds/                           agents, tools, permission defaults
├── docker/                              Dockerfiles
├── docs/{architecture,api,agents,security,development}/
├── scripts/                             dev, seed, node install
├── tests/{unit,integration,e2e}/
├── docker-compose.yml · pyproject.toml · settings.yaml · .env.example
```

**Import rules** (unchanged from V1, now enforceable by package boundary):

1. `thursday_shared` imports nothing else from Thursday.
2. `thursday_core` imports `shared` only — never `api`, never a vendor SDK.
3. Adapters import `shared` plus their SDK.
4. `apps/*` and `services/*` wire things together; they hold no domain logic.

---

## E. Database ER Model

25 tables, already migrated. V2's domain model (PART 4) maps onto them with three additions.

```
users ─┬─< devices ─< device_credentials          [new: pairing, key rotation]
       ├─< projects ─< tasks ─< task_steps ─< tool_runs
       │                  │                 ─< agent_runs
       │                  └─< approvals
       ├─< memories ─< memory_relations           [new: supersedes|contradicts|updates]
       ├─< entities ─< relationships
       ├─< skills ─< skill_versions ─< skill_runs
       ├─< automations · events · notifications
       ├─< permissions ─< permission_grants
       ├─< audit_logs        (append-only, hash-chained)
       ├─< observations      (vision metadata only; no frames)
       └── world_state       (1 row per user, mirrored in Redis for realtime)
```

New columns this pass: `tasks.autonomy_level`, `tools.supports_dry_run`,
`tools.supports_undo`, `approvals.policy` (`ASK_ONCE`|`ASK_ALWAYS`).

---

## F. Core Interfaces

Unchanged ports: `LLMProvider`, `STTProvider`, `TTSProvider`, `WakeWordProvider`,
`VectorProvider`, `EmbeddingProvider`, `MemoryProvider`, `ToolProvider`, `AgentProvider`,
`DeviceProvider`, `VisionProvider`, `SecretProvider`, `EventBus`, `ExecutionContext`.

Added for V2:

```python
class ThursdayCore:
    async def handle_request(self, request: UserRequest) -> ThursdayResponse: ...


class QueueProvider(Protocol):  # Dramatiq in production, in-process in tests
    async def enqueue(self, actor: str, **kwargs) -> str: ...
    async def cancel(self, job_id: str) -> bool: ...


class StateStore(Protocol):  # Redis in production, in-memory in tests
    async def get(self, key: str) -> Any: ...
    async def set(self, key: str, value: Any, *, ttl_s: float | None = None) -> None: ...
    async def publish(self, channel: str, message: dict) -> None: ...
```

---

## G. Agent Protocol

`AgentJob` in, `AgentResult` out (PART 14). `AgentRouter.select` returns an
`AgentSelection` carrying its score breakdown, so a routing decision is explainable rather
than merely made. `SupervisorResult` gains `quality_score` and `issues` (PART 15).

Supervision is **mandatory**, not discretionary, when the work involves: a calculation, an
important file, an external action, a destructive action, a report, or a code change.

---

## H. Device Protocol

TNP/1 stays — outbound WebSocket, signed HELLO, heartbeat, correlated action/result.
Commands become namespaced per PART 23; the envelope gains `message_id` and `signature`
per PART 24:

```
system.info · system.process.list|start|stop · app.open
file.search|read|create|copy|move|rename|delete · screen.capture · window.active
clipboard.read|write · audio.volume.get|set · powershell.run · browser.open
```

Verification stays mandatory: `app.open` succeeds only when the process is *observed*.

---

## I. Permission Matrix

| Command | Level | Risk | Policy |
|---|---|---|---|
| `file.read` `file.search` `system.info` `system.process.list` `window.active` `clipboard.read` `audio.volume.get` | READ | LOW | AUTO |
| `app.open` `browser.open` `screen.capture` | OPEN | LOW | AUTO |
| `file.create` `clipboard.write` `audio.volume.set` | MODIFY | LOW | AUTO |
| `file.copy` | MODIFY | LOW | AUTO, bulk → ASK_ONCE |
| `file.move` `file.rename` | MODIFY | MEDIUM | AUTO if reversible, bulk → ASK_ONCE |
| `file.write` (existing document) | MODIFY | MEDIUM | AUTO **with version backup** |
| `file.delete` | MODIFY | HIGH | **ASK_ALWAYS** |
| `system.process.stop` | MODIFY | MEDIUM | ASK_ONCE |
| `powershell.run` | MODIFY→SYSTEM | HIGH | **ASK_ALWAYS**, risk-analyzed |
| `email.send` `message.send` `social.post` `purchase` | EXTERNAL | HIGH–CRITICAL | **ASK_ALWAYS** |
| `system.setting.write` `app.install` `service.control` | SYSTEM | HIGH | **ASK_ALWAYS** |
| `shell.admin` `credential.change` | ADMIN | CRITICAL | **ASK_ALWAYS** + re-auth |
| `security.disable` `credential.export` `audit.modify` | — | CRITICAL | **BLOCK**, no override path |

---

## J. First Vertical Slice Plan

PART 77's seventeen steps — already passing end to end for text, and the target for voice.
The acceptance test (PART 78) asserts each numbered step and, more importantly, asserts the
negative case: **if Chrome fails to start, the task must not be COMPLETED.**

That negative assertion is what proves the design. Everything else is plumbing.

---

## K. Files to Create

Ordered per PART 100. ✅ marks what exists and is being *aligned* rather than written.

| # | Item | State |
|---|---|---|
| 1 | `pyproject.toml` | ✅ → Python 3.12+, locked stack, workspace packages |
| 2 | `docker-compose.yml`, `docker/*` | new |
| 3 | `settings.yaml`, `.env.example` | ✅ → config groups |
| 4 | `services/api/` | ✅ → V2 paths, health sub-endpoints |
| 5 | `database/migrations` ✅ + `database/seeds/` | seeds new |
| 6 | task state machine | ✅ → `+READY`, `+PAUSED` |
| 7 | `ToolDefinition` | ✅ → `supports_dry_run`, `supports_undo` |
| 8 | `AgentJob`/`AgentResult`, `AgentRouter` | ✅ → V2 field names |
| 9 | permission engine | ✅ → `ASK_ONCE`/`ASK_ALWAYS`, namespaced matrix |
| 10 | Windows node | ✅ → namespaced commands, signed envelope |
| 11 | Computer Agent | ✅ → namespaced commands |
| 12 | `FakeDeviceNode` | promoted from test fixture to shipped artifact |
| 13 | Supervisor | ✅ → `SupervisorResult`, mandatory-supervision rules |
| 14 | WebSocket | ✅ → V2 event names |
| 15 | `apps/desktop/` | real Tauri + React + Tailwind app |
| 16 | tests | ✅ → `tests/e2e/`, slices 2–5 |
