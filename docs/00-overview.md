# Thursday — Personal AI Operating System

> One assistant, many capabilities.

Thursday is a **personal AI operating system**, not a chatbot. The user talks to exactly one
identity — Thursday. Everything else (agents, tools, devices, models) is machinery that
Thursday drives on the user's behalf.

```
USER → THURSDAY → Understand → Plan → Delegate → Act → Verify → Remember → Report
```

## Documents

| # | Deliverable | Document |
|---|---|---|
| 1 | System Architecture | [01-architecture.md](01-architecture.md) |
| 2 | Technology Stack | [02-tech-stack.md](02-tech-stack.md) |
| 3 | Repository Structure | [03-repository-structure.md](03-repository-structure.md) |
| 4 | Database Schema | [04-database-schema.md](04-database-schema.md) |
| 5 | Core Interfaces | [05-core-interfaces.md](05-core-interfaces.md) |
| 6 | Agent Architecture | [06-agent-architecture.md](06-agent-architecture.md) |
| 7 | Memory Architecture | [07-memory-architecture.md](07-memory-architecture.md) |
| 8 | Permission Model | [08-permission-model.md](08-permission-model.md) |
| 9 | Device Protocol | [09-device-protocol.md](09-device-protocol.md) |
| 10 | Event Architecture | [10-event-architecture.md](10-event-architecture.md) |
| 11 | API Specification | [11-api-spec.md](11-api-spec.md) |
| 12 | MVP Scope | [12-mvp-scope.md](12-mvp-scope.md) |
| 13 | Implementation Roadmap | [13-roadmap.md](13-roadmap.md) |
| 14 | Security Threat Model | [14-threat-model.md](14-threat-model.md) |
| 15 | First Vertical Slice | [15-vertical-slice.md](15-vertical-slice.md) |
| — | Thursday's persona contract | [16-persona.md](16-persona.md) |

## Non-negotiable design rules

These are enforced in code, not just documented.

1. **Verify before reporting success.** No agent output and no device action is trusted
   because it was dispatched. `VERIFY` is a task state, and `Supervisor` gates completion.
   (`thursday/core/supervisor.py`, `TaskState.VERIFYING`)
2. **Least privilege, explicit permission.** Every tool call passes the Permission Engine.
   Risky actions are `ASK`; forbidden actions are `BLOCK`. (`thursday/security/permissions.py`)
3. **Secrets never enter a prompt, a note, or a vector store.** Credentials live behind
   `SecretVault` and are referenced by handle. (`thursday/security/vault.py`)
4. **Privacy classification decides where inference runs.** `SECRET` never leaves the
   machine. (`thursday/security/privacy.py` + `thursday/core/model_router.py`)
5. **Memory is curated, not a transcript dump.** Conversation context ≠ long-term memory.
   (`thursday/memory/`)
6. **Everything is observable and reversible where possible.** Audit log for every action;
   undo records for every reversible one. (`thursday/security/audit.py`, `thursday/core/undo.py`)
7. **Providers are swappable.** LLM/STT/TTS/vector/tool/device are interfaces with at least
   two implementations each, one of which runs fully offline.
