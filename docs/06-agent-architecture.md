# 6. Agent Architecture

## 6.1 Principle

**Thursday is the only identity the user talks to** (§94). Agents are workers; they never
address the user directly. Agent output flows back to Thursday, which decides what — if
anything — the user needs to hear.

## 6.2 Selection (§14)

The user never picks an agent. `AgentOrchestrator` scores candidates from the
`CapabilityRegistry`:

```
score = capability_match * 0.5
      + device_affinity  * 0.2      (is the needed device online & capable?)
      + historic_success * 0.15     (procedural memory)
      - cost_norm        * 0.1
      - risk_norm        * 0.05
```
Ties break toward the lower-privilege, lower-cost agent. If no agent scores above
`min_confidence`, Thursday asks a clarifying question instead of guessing.

## 6.3 Job contract (§17)

No agent is invoked without a `JobContract` fixing objective, inputs, output schema,
success criteria, permission set, deadline and budget. An agent that returns output not
matching `output_schema` fails at the boundary — the Supervisor never sees malformed data.

## 6.4 Supervision loop (§18)

```
        ┌──────────────────────────────────────────┐
        ▼                                          │ RETRY (bounded, n≤2, with critique)
  Agent.run(contract) ──► AgentResult ──► Supervisor.verify(contract, result)
                                              │
                        ┌─────────────────────┼─────────────────────┐
                        ▼                     ▼                     ▼
                      PASS                 RETRY               ESCALATE
                        │                                          │
                 mark step done                        ask user / alternative agent
```

Supervisor checks, in order: **schema → completeness → internal consistency (arithmetic,
totals, counts) → source provenance → safety → success criteria**. Cheap deterministic
checks run before any LLM critique, so most verification costs nothing.

Retries are bounded and *informed*: the critique text is appended to the next contract.
Infinite retry is prohibited (§96).

## 6.5 Default agents (§15)

| Agent | Capabilities | Default permission ceiling |
|---|---|---|
| `research` | web search, source comparison, fact-check | L0 read + network |
| `computer` | files, apps, OS actions via device node | L2 modify (L3+ needs approval) |
| `browser` | navigation, forms, web app automation | L2 |
| `data` | csv/xlsx/db, statistics, charts, cleaning | L2 on scoped paths |
| `document` | docx/pdf/report generation, summarization | L2 |
| `coding` | code, debug, scripts (sandboxed) | L2 in workspace only |
| `design` | images, posters, slides, UI assets | L2 |
| `media` | audio/video processing | L2 |
| `calendar` | schedule, reminders, conflicts | L3 (ASK on writes) |
| `communication` | email/message drafting + send | **L3, always ASK on send** |
| `vision` | camera, OCR, detection, scene understanding | L0 + camera permission |
| `automation` | rule creation and execution | L3 |
| `supervisor` | validate, critique, verify | L0 read-only, always |

The Supervisor is deliberately **read-only**: a verifier that can edit is not a verifier.

## 6.6 Dynamic agents (§16)

`AgentFactory.create_temporary(spec)` mints an agent with name, goal, system prompt, tool
subset, permission subset (never exceeding the parent task's), IO schemas, success
criteria, timeout and budget. Guards:

- max 4 dynamic agents per task, depth ≤ 2 (an agent may not spawn a spawner) — §96
- inherited permissions are intersected, never unioned
- destroyed on task completion; the *spec* may be promoted to a Skill if it proved useful

## 6.7 Failure handling (§60)

`retry (if idempotent & safe) → alternative tool → alternative agent → ask the user`.
Every hop is logged with the reason. The escalation to the user carries what was tried.
