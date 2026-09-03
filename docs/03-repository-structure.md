# 3. Repository Structure

The brief's structure (§77) is a *logical* map. This repo implements it as one installable
Python distribution with subpackages, plus thin app entrypoints — because a dozen separately
versioned Python packages buys nothing until there are a dozen deployment targets, and it
costs a working `pytest` today. The mapping is 1:1 and the split can be done later without
moving a single import.

```
thursday-v3/
├── apps/
│   ├── server/          # FastAPI entrypoint (uvicorn target)
│   ├── node/            # device node agent (Windows/macOS/Linux)
│   ├── cli/             # terminal client: chat + voice-loop simulation, approvals
│   ├── desktop/         # Tauri 2 + React shell (orb, conversation, approvals)  [scaffold]
│   └── mobile/          # Flutter remote (voice, approvals, device control)      [planned]
│
├── thursday/            # ← "packages/*" from §77 live here as subpackages
│   ├── shared/          # packages/shared   – types, interfaces (ports), errors, ids
│   ├── core/            # packages/core     – engine, context, planner, orchestrator,
│   │                    #                     world state, routers, supervisor, undo
│   ├── agents/          # packages/agents   – base agent, registry, default agents
│   ├── tools/           # packages/tools    – tool registry + built-in tools
│   ├── memory/          # packages/memory   – layered memory, vector store, obsidian, KG
│   ├── vision/          # packages/vision   – camera, OCR, gestures, spatial memory
│   ├── voice/           # packages/voice    – wake word, VAD, STT, TTS, voice modes
│   ├── devices/         # packages/devices  – node protocol, registry, hub
│   ├── security/        # packages/security – permissions, policy, vault, privacy, audit
│   ├── automation/      # packages/automation – events, rules, scheduler, routines
│   ├── skills/          # packages/skills   – skill model, registry, learning, sandbox
│   ├── api/             # services/api      – routers, schemas, ws
│   ├── worker/          # services/worker   – queue consumer, background jobs
│   └── db/              # services/database – models, session, migrations
│
├── docs/                # the 15 design deliverables
├── tests/
│   ├── unit/
│   └── integration/     # end-to-end vertical slice tests
├── pyproject.toml
└── README.md
```

## Import rules (enforced by review, checkable by lint)

1. `thursday.shared` imports nothing from Thursday.
2. `thursday.core` may import `shared` only — never `api`, never a provider SDK.
3. Providers/adapters may import `shared` + their SDK.
4. `apps/*` wire things together; they contain no domain logic.
5. Anything crossing a process boundary is a Pydantic model in `thursday/shared/`.
