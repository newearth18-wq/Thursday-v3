# 1. System Architecture

## 1.1 Layer map

```
                       ┌───────────────────────────────────────────┐
  INPUT                │ voice · text · camera · gesture · screen   │
                       │ files · notifications · sensors            │
                       └──────────────────┬────────────────────────┘
                                          │
                       ┌──────────────────▼────────────────────────┐
  INTERFACE            │ Desktop (Tauri) · Mobile · CLI · WS/REST   │
                       └──────────────────┬────────────────────────┘
                                          │  ConversationTurn
┌─────────────────────────────────────────▼─────────────────────────────────────────┐
│ THURSDAY CORE (stateless per-request, state in Postgres/Redis)                     │
│                                                                                    │
│  ConversationEngine ─► ContextEngine ─► ReasoningEngine ─► Planner ─► Orchestrator  │
│         │                   ▲                 │                          │         │
│         │            WorldState ◄─────────────┘                          │         │
│         │                   ▲                                            │         │
│         ▼                   │                                            ▼         │
│  MemoryManager ─────────────┘                              ToolRouter · DeviceRouter│
│  GoalManager · TaskManager · EventEngine · AutomationEngine · Supervisor            │
│  PermissionEngine · ModelRouter · SkillRegistry · CapabilityRegistry                │
└─────────────┬──────────────────────────────────────────────┬──────────────────────┘
              │ EventBus (in-proc → Redis Streams)            │
┌─────────────▼─────────────────┐              ┌──────────────▼──────────────────────┐
│ AGENT NETWORK                 │              │ DEVICE + TOOL LAYER                 │
│ research · computer · browser │              │ Windows/macOS/Linux nodes           │
│ data · document · coding      │              │ Android node · browser              │
│ design · media · calendar     │              │ Google · storage · Obsidian         │
│ communication · vision        │              │ HTTP APIs · IoT                     │
│ automation · supervisor       │              │                                     │
└───────────────────────────────┘              └─────────────────────────────────────┘
```

## 1.2 The request lifecycle

Every user utterance goes through exactly one path. This is implemented in
`thursday/core/engine.py::ThursdayEngine.handle_turn`.

```
1  INGEST      normalize input (text / STT transcript / vision event) → ConversationTurn
2  CONTEXT     ContextEngine builds a ContextPackage:
               conversation window + world state + active project/task + retrieved memory
               + device capabilities + screen/selection/clipboard (if permitted)
3  CLASSIFY    PrivacyClassifier assigns a DataSensitivity to the turn + its context
4  ROUTE MODEL ModelRouter picks a model tier from complexity × privacy × latency × cost
5  UNDERSTAND  ReasoningEngine produces an Intent:
               { kind, objective, entities, referenced_device, confidence, needs_plan }
6  DECIDE      • answer directly            → 9
               • single tool call           → Planner emits a 1-step plan
               • multi-step / multi-agent   → Planner emits a Plan (DAG of steps)
7  AUTHORIZE   PermissionEngine evaluates every step: AUTO | ASK | BLOCK
               ASK  → Task enters WAITING_APPROVAL, user is asked, execution resumes on grant
               BLOCK→ refuse, explain, log
8  EXECUTE     Orchestrator runs the plan. Each step is a JobContract given to
               an agent, a tool, or a device node. Long work runs on the worker queue.
9  VERIFY      Supervisor validates output against the contract's success criteria
               → PASS | RETRY (bounded) | ESCALATE
10 REMEMBER    MemoryManager decides *whether* to persist, at what layer, with what
               importance/confidence/source. Conflicts are recorded, never silently merged.
11 REPORT      ResponseComposer renders Thursday's answer (short first, detail on request),
               chooses a VoiceMode, and routes output to the right device.
```

Steps 3, 7, 9 and 10 are the ones that make this an operating system rather than a chat
loop, and they are the ones that are hardest to retrofit — so they exist from commit one.

## 1.3 Component responsibilities

| Component | Owns | Explicitly does NOT own |
|---|---|---|
| `ConversationEngine` | turn history window, streaming, interruption | long-term memory |
| `ContextEngine` | assembling the context package | deciding what to do |
| `ReasoningEngine` | intent + answer generation via `LLMProvider` | tool execution |
| `Planner` | turning an intent into a step DAG | running steps |
| `Orchestrator` | scheduling, retries, agent selection | judging quality |
| `Supervisor` | judging quality against success criteria | fixing the work |
| `MemoryManager` | write policy, retrieval, conflict handling | storage engine details |
| `WorldState` | "now" facts: device, app, project, task, presence | history |
| `PermissionEngine` | AUTO/ASK/BLOCK verdicts | performing actions |
| `ToolRouter` | tool selection by capability/cost/risk/privacy | tool implementations |
| `DeviceRouter` | resolving "this machine" / "the laptop" to a node | device I/O |
| `ModelRouter` | model tier selection + fallback | prompt construction |
| `EventEngine` | event fan-out, subscriptions | business logic |
| `AutomationEngine` | WHEN/DO/THEN rules, schedules | ad-hoc tasks |

## 1.4 Boundaries that keep providers swappable

Core depends only on the protocols in `thursday/shared/interfaces.py`. Concrete providers
(`AnthropicLLM`, `OllamaLLM`, `RuleBasedLLM`, `WhisperSTT`, `PiperTTS`, `PgVectorStore`,
`SqliteVectorStore`…) are constructed once in `thursday/core/container.py` (the DI
container) from config, and injected. No core module imports a vendor SDK. Swapping a
provider is a config change plus one adapter file.

## 1.5 Execution topology

- **Core API** (`apps/server`) — FastAPI: REST + `WS /realtime`. Holds no device I/O.
- **Worker** (`thursday/worker`) — long-running tasks, automations, memory consolidation.
- **Device Nodes** (`apps/node`) — one process per machine. *Outbound* WebSocket to core
  (so no inbound firewall holes), authenticated per device, capability-advertising.
- **Clients** — desktop/mobile/CLI. Thin: render, capture, approve. No business logic.

State lives in Postgres (durable) and Redis (ephemeral/queue). A device or client can die
and reconnect without losing a task, because tasks are core-side objects with UUIDs
(§23 multi-device continuity).
