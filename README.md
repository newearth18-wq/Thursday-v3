# Thursday

> **One assistant, many capabilities.**
> A personal AI operating system — not a chatbot, and not a collection of agents wearing a
> trench coat.

The owner talks to exactly one identity: **Thursday**. Everything behind it — specialist
agents, tools, models, devices — is machinery Thursday drives on their behalf.

```
USER → THURSDAY → Understand → Plan → Delegate → Act → Verify → Remember → Report
```

---

## Status

**Phase 1 is implemented and runnable**: the vertical slice from
[docs/15-vertical-slice.md](docs/15-vertical-slice.md) works end to end, with 814 tests that
need no database, no network and no model credentials.

```
$ python -m apps.cli --device-name Office-PC

you> Thursday เปิด chrome
Thursday> เปิด chrome เรียบร้อย (ยืนยันแล้ว)
          [SUCCESS · confidence 1.00]

you> Thursday เปิด xcalc
Thursday> ผมทำสิ่งนี้ไม่ได้ — FileNotFoundError: no executable found for 'xcalc' on this machine
          [WARNING · confidence 1.00 · unverified]
```

That second answer is the point. Thursday does not say a thing worked because a command was
sent; it says what it *observed*.

---

## Quick start

```bash
uv venv && source .venv/bin/activate      # or: python -m venv .venv
uv pip install -e ".[dev]"
alembic upgrade head                      # SQLite by default; no server needed

python -m apps.cli                        # embedded core + local node, one command
```

Three-process setup (how it runs for real):

```bash
# 1. One enrolment token, shared by the core and every node (ADR 0013).
#    The core refuses an unsigned or wrongly-signed HELLO in every environment,
#    so this is not optional and there is no development bypass.
export THURSDAY_SECRET_DEVICE_ENROLLMENT_SECRET=$(python -c "import secrets;print(secrets.token_urlsafe(32))")

python -m apps.server                                  # core API on :8000
python -m apps.node --name Office-PC --allow-root ~    # one per machine

# 2. Then pair each node, which gives it its own key and closes the shared token
#    for that machine permanently (ADR 0029). The node prints a code and its key
#    fingerprint; confirm both in Thursday. Afterwards the node no longer needs
#    THURSDAY_SECRET_DEVICE_ENROLLMENT_SECRET set at all.
python -m apps.node --pair --name Office-PC
python -m apps.cli --remote                            # or the desktop app

cd apps/desktop && npm install && npm run tauri dev    # the window
```

Check a node from the machine it runs on — this listens on loopback and executes nothing,
so it still answers when the node cannot reach the core:

```bash
curl -s localhost:8765/health | jq        # connected_to_core, last_error, allowed_roots
curl -s localhost:8765/capabilities | jq  # what this node will actually do
```

Then send the command:

```bash
curl -s localhost:8000/api/v1/conversations \
  -H 'content-type: application/json' \
  -d '{"text": "Thursday เปิด Chrome"}' | jq

# {"text": "เปิด chrome เรียบร้อย (ยืนยันแล้ว)", "status": "COMPLETED", "verified": true, ...}
```

`verified: true` is the part that matters. It means the node looked for the process after
launching it and found it — not that the launch command returned without an error.

Reconstruct any single turn from the trail:

```bash
curl -s "localhost:8000/api/v1/audit?trace_id=$TRACE" | jq '.entries[] | {tool, action, result}'
```

With Postgres, Redis and a separate worker:

```bash
docker compose up -d          # postgres+pgvector, redis, api, worker
./scripts/dev.sh              # or run the pieces locally
```

Nothing above reaches for the cloud. The default model backend is a deterministic offline
tier; point `THURSDAY_LLM_BACKEND` at `ollama` or `anthropic` when you want reasoning.
See [.env.example](.env.example).

### CLI commands

`/devices` `/approvals` `/approve <n> [always]` `/reject <n>` `/tasks` `/memory <query>`
`/audit` `/undo` `/world` `/health` `/stop` `/help`

---

## The nine rules the code enforces

These are the design, and they are tested rather than documented and hoped for.

Paths below are relative to `packages/<name>/thursday_<name>/`.

| # | Rule | Where it lives |
|---|---|---|
| 1 | **Verify before reporting success.** Dispatch is not success. | `devices/node/executor.py`, `core/supervisor.py`, `core/tasks.py::complete` |
| 2 | **Least privilege, explicit permission.** Every action passes the engine; the BLOCK set has no override path — not by config, not by grant, not by an agent's own reasoning. | `security/permissions.py`, `security/policy.py` |
| 3 | **Secrets never enter a prompt, a note, a log or a vector store.** | `security/vault.py`, `security/redaction.py` |
| 4 | **Privacy decides where inference runs.** `SECRET` never leaves the machine, and an agent that could reach the cloud is excluded rather than penalised. | `security/privacy.py`, `core/model_router.py`, `agents/registry.py` |
| 5 | **Memory is curated, not a transcript.** Conflicts are recorded, never merged, and an agent cannot write the owner's preferences. | `memory/manager.py` |
| 6 | **Everything is audited and, where possible, reversible.** | `security/audit.py`, `core/undo.py` |
| 7 | **Providers are swappable.** Every port has a real adapter and an offline one. | `shared/interfaces.py`, `core/container.py` |
| 8 | **Thursday proposes; the owner decides.** Learned routines arrive disabled; risky skills cannot self-activate. | `automation/routines.py`, `automation/skills/registry.py` |
| 9 | **Untrusted content is data, never instruction.** A page or a file cannot widen what Thursday may do. | `agents/browser.py`, [ADR 0010](docs/architecture/decisions/0010-untrusted-content-is-data.md) |

Rule 1, concretely:

```python
# packages/core/thursday_core/tasks.py
if not verification.passed:
    raise ThursdayError("refusing to complete a task whose verification did not pass")
```

---

## Architecture at a glance

```mermaid
flowchart TB
    subgraph clients [" "]
        direction LR
        UI["Desktop app · CLI · API"]
    end

    subgraph core ["Thursday Core — one lifecycle, no step skippable"]
        direction TB
        U["Understand<br/><i>rules first, model second</i>"]
        P["Plan"]
        A["Authorize<br/><i>PermissionEngine · ADR 0011</i>"]
        X["Execute<br/><i>via an agent</i>"]
        V["Verify<br/><i>observe, don't assume · ADR 0012</i>"]
        R["Remember + Report"]
        U --> P --> A --> X --> V --> R
    end

    subgraph agents ["Agents"]
        direction LR
        CA["Computer"]
        RA["Research"]
        BA["Browser"]
        SUP["Supervisor<br/><i>read-only</i>"]
    end

    subgraph devices ["Devices — the only code that touches an OS · ADR 0014"]
        direction LR
        N1["Windows node"]
        N2["macOS node"]
        N3["Linux node"]
        FN["FakeDeviceNode<br/><i>tests + --fake-device</i>"]
    end

    UI -->|"HTTP · WebSocket"| U
    X --> agents
    agents -->|"ToolCall"| A
    A -->|"AUTO"| devices
    A -->|"ASK"| APR["Approval<br/><i>nothing runs while this is open</i>"]
    APR -->|"owner decides"| devices
    devices -->|"result + evidence"| V
    V --> SUP
    SUP -->|"pass"| R
    SUP -->|"fail"| FAIL["Task FAILED<br/><i>reported as unverified,<br/>never as success</i>"]

    devices -.->|"outbound WebSocket<br/>ADR 0015"| core
    R --> AUD[("Audit — hash-chained")]
```

The one path worth tracing: **nothing reaches a device without passing Authorize, and
nothing completes without passing Verify.** Both are single choke points rather than
conventions, so neither can be forgotten by a new caller.

Full design in [`docs/`](docs/) — the twenty-three deliverables, written before the code,
plus the [V2 review](docs/architecture/00-v2-review.md) and twenty-nine
[architecture decisions](docs/architecture/decisions/) recording what was chosen and what
each choice cost:

| | | | |
|---|---|---|---|
| [Architecture](docs/01-architecture.md) | [Stack](docs/02-tech-stack.md) | [Repo layout](docs/03-repository-structure.md) | [Database](docs/04-database-schema.md) |
| [Interfaces](docs/05-core-interfaces.md) | [Agents](docs/06-agent-architecture.md) | [Memory](docs/07-memory-architecture.md) | [Permissions](docs/08-permission-model.md) |
| [Device protocol](docs/09-device-protocol.md) | [Events](docs/10-event-architecture.md) | [API](docs/11-api-spec.md) | [MVP scope](docs/12-mvp-scope.md) |
| [Roadmap](docs/13-roadmap.md) | [Threat model](docs/14-threat-model.md) | [Vertical slice](docs/15-vertical-slice.md) | [Persona](docs/16-persona.md) |
| [Voice](docs/17-voice.md) | [Vision](docs/18-vision.md) | [Gesture](docs/19-gestures.md) | [Multi-device](docs/20-multi-device.md) |
| [Agents & skills](docs/21-agents-and-skills.md) | [Proactive](docs/22-proactive.md) | | |

---

## What is built, and what is not

**Built and working**

- Full request lifecycle in one place (`core/engine.py`), so no step can be skipped
- Permission engine, action policy, scoped expiring grants, approval flow, hash-chained audit
- Device node protocol (TNP/1) with Windows, macOS and Linux adapters, a path jail, and
  ACT→VERIFY on every action
- Task state machine with budgets, bounded informed retries, and a queue
- Layered memory with an explicit write policy, conflict recording and decay — and
  remembered instructions that are *applied* to later work rather than only recalled,
  with "forget about X" and "don't remember this" as first-class commands
- Obsidian vault that *refuses* credential material rather than redacting it, with
  durable knowledge mirrored into it automatically and passing detail left out
- Agent orchestrator running a real DAG — independent steps in parallel, each step's
  output passed to its dependents — with capability-based selection and a read-only
  Supervisor whose arithmetic checks now have figures to check
- Dynamic agents with intersected permissions, depth and count caps, destroyed with the task
- Automation engine, proactivity gate with rate limits, routine learning that **proposes**
- Skills: capture → sandbox test → approval → activate → rollback, with risky steps
  gated; retrieved by what the owner *says* ("แบบที่เคยทำ"), turned into a plan, and
  composable into new skills that re-enter the lifecycle as drafts
- Thirteen specialist agents plus the Supervisor. Most sit at a READ ceiling on purpose:
  `coding` proposes patches and never applies or runs one, `file` finds duplicates and never
  deletes them, and `communication` drafts messages with **no code path to a sent one** —
  the single action here with neither an undo nor a verification
- Data and document agents that compute every figure in Python and refuse to pass off a
  report that does not contain the numbers it was written from
- Skills learned by watching: the same ordered sequence, seen twice, with the arguments that
  varied recovered as its inputs — proposed as a draft, never activated
- Proactive: observations that become *offers* rather than actions unless read-only,
  reversible and local; "ทำเลย" accepts one and it becomes ordinary work through the same
  planner, permissions and Supervisor; goals above tasks with preemption that pauses rather
  than cancels; morning and end-of-day briefs assembled from state, never generated; a
  decision journal that records the alternatives
- Learning that will not run away with itself: one correction is an event, not a preference —
  only a repeated, recent, consistent signal becomes a *question*. Self-recovery may restore
  a capability and may never widen one, refused at wiring time rather than at call time
- Vision: camera consent that is provable rather than promised, frame sampling that
  never lets a stream leave the machine, screen reading, "what is this?" resolution,
  and spatial memory that answers as a *sighting* with its age said out loud
- Gesture: pointing, pinch, drag, swipe and two-hand zoom off a four-state mode that is
  closed by default, a cooldown so a held gesture is one command rather than thirty, and
  a safety gate under which **no gesture alone** can confirm a deletion, a payment, a
  system change or anything that leaves the machine
- Multi-device: a conversation that remembers which machine it is about and *says* which
  one it acted on, task handoff between devices, presence-based output routing, per-device
  trust, and a remote-command gate under which an instruction crossing machines is a
  different instruction — refused from an unvouched device, asked about when it writes,
  and audited at both ends
- Model router with tiers, cost/privacy routing and a local fallback
- Realtime voice: wake word → VAD → STT → core → verification → TTS, with a
  state machine, working barge-in, per-mode prosody, audio routing and provider
  fallback that survives a network failure mid-utterance
- Background worker: memory decay, health, device liveness, approval expiry
- 80 REST operations, two WebSockets, 29-table schema with working migrations and seeds

**Designed, ported, not yet implemented** — every one has an interface and a Phase in
[the roadmap](docs/13-roadmap.md):

- SQL-backed repositories behind the memory/task/audit ports (they run in-process today;
  the schema and migrations are in place). This is the largest gap.
- Redis event bus and worker queue (the in-process bus implements the same port)
- Real camera capture, a local object detector, OCR and barcode decoding. The vision
  pipeline above them — consent, sampling, reading, reference resolution, spatial memory —
  is built and tested end to end against synthetic frames; the adapters for opencv,
  ultralytics, tesseract and zbar are written but unexercised without the packages
- Real microphone and speaker capture. The voice loop, its state machine, barge-in,
  routing and fallback are built and tested end to end against synthetic audio; what is
  missing is the hardware layer (`sounddevice`) and a cloud provider at the head of the
  chain. faster-whisper and Piper adapters are written but unexercised without model files
- Real calendar and mail accounts. `CalendarProvider` and `MessageProvider` are ports with
  local adapters — real behaviour, nothing leaving the machine, and *not* the owner's actual
  calendar or inbox. A Google or Outlook adapter is a new class behind the same protocol.
- Media editing of any kind. No Pillow, no ffmpeg, no codec, so the media agent identifies
  files from their headers and says outright that it cannot convert, resize or generate.
  The design agent writes specifications with contrast computed, and does not draw.
- Mobile client (scaffold in `apps/mobile`). The desktop app is built — conversation,
  approvals, tasks, devices, memory and permissions — but has no voice capture and no
  embedded device node yet
- Key storage in the OS keychain. Per-device Ed25519 keys are built and enforced
  (ADR 0029) — a node generates its own keypair, pairs with proof of possession plus a
  code a person confirms, and from then on the shared token is closed for that machine —
  but the private key lives in a 0600 file rather than in Keychain, DPAPI or the Secret
  Service, and the pairing registry is in memory rather than in `device_credentials`
- The MediaPipe hand-landmark source. Gesture classification, the mode machine, the safety
  gate and speech+gesture fusion are built and tested against landmarks constructed
  directly; nothing has yet read a real hand, and this container has no camera to read one

Nothing is faked at a layer where faking would hide a design problem. The permission checks,
the verification loop, the audit chain and the device round-trip are all real.

---

## Development

```bash
./scripts/check.sh           # everything CI runs: lint, format, types, tests, migrations
pytest                       # 814 tests, no infrastructure
ruff check . && ruff format .
mypy packages services
alembic upgrade head && alembic revision --autogenerate -m "what changed"
python -m database.seeds     # idempotent: one owner, six agents, the tool catalogue
```

`thursday_devices.fake` ships a `FakeDeviceNode` whose `fail_launch=True` mode makes a
command appear to succeed while nothing actually starts — the case rule 1 exists for. It is
a shipped artifact rather than a fixture, because the CLI's `--fake-device` mode and anyone
extending the node protocol need the same misbehaving node the tests use.

## Layout

```
apps/        server · node · cli · worker · desktop (Tauri) · mobile (planned)
packages/    shared · core · agents · tools · memory · devices · security
             voice · vision · automation · models
services/    api · realtime · worker
database/    migrations · seeds
docker/      api and node images; docker-compose.yml at the root
docs/        the twenty-three design deliverables + architecture/decisions (ADRs)
tests/       unit · integration · e2e
scripts/     dev.sh · check.sh · check_no_secrets.py
```

## Licence

MIT.
