# 14. Security Threat Model

Thursday holds a microphone, a camera, the user's files, their credentials, and the ability
to act on their behalf. It is one of the highest-value targets a person could run.
Assume compromise attempts; design for containment.

## 14.1 Assets

Owner's files · credentials & tokens · conversation and memory · camera/mic streams ·
device control · calendar/email · the audit log · Thursday's own permission tables.

## 14.2 Adversaries

| Actor | Capability |
|---|---|
| Remote attacker | network reach to core/API |
| Malicious content | a web page, email, PDF, or filename Thursday reads |
| Compromised dependency | a model provider, a Python package, an MCP/tool server |
| Compromised device node | a machine that was stolen or rooted |
| Curious bystander | physical access to a logged-in session |
| Thursday itself | a plausible-but-wrong plan executed confidently |

## 14.3 Threats and mitigations

| # | Threat | Mitigation |
|---|---|---|
| T1 | **Prompt injection** in fetched web/email/file content causes an action | Untrusted content is data, never instruction: it enters the prompt inside a delimited untrusted block; tool calls derived from untrusted text are re-evaluated against the *user's* intent; L3+ always requires human approval; a plan step whose justification traces only to untrusted content is refused. Agents cannot escalate their own permissions. |
| T2 | **Secret exfiltration** via prompt or note | Secrets live in the OS keychain behind `SecretVault`; the LLM sees handles, never values; `SecretRedactor` runs on every prompt, vault write, memory write, and log line; egress of a matched secret pattern is blocked. |
| T3 | **Device node impersonation** | Ed25519 per-device keys, out-of-band pairing, TLS pinning, nonce challenge at `HELLO`, revocation list, short-lived session tokens. |
| T4 | **Stolen device** | Node key in OS keychain; `POST /emergency/stop` revokes tokens and kills sessions; per-device trust levels; L4/L5 needs re-auth regardless of standing grants. |
| T5 | **Over-broad standing grants** | "Always allow" is scoped (action × resource glob × device) and expires; no global grant exists; grants are listed and revocable in one screen. |
| T6 | **Destructive action from a wrong plan** | Dry run + approval bound to the report hash; undo records; bounded blast radius (object-count thresholds trigger ASK); sandbox-first for shell/scripts. |
| T7 | **Runaway cost / infinite retry** | Per-task budgets (tokens, USD, seconds, agent/tool calls); retry cap of 2 with critique; dynamic-agent depth and count caps; circuit breaker per provider. |
| T8 | **Privacy leak to cloud** | `PrivacyClassifier` on every payload; `SECRET` never leaves the machine — enforced in `ModelRouter`, not by convention; per-zone camera/mic/cloud/memory switches; local-first for `HIGHLY_PRIVATE`. |
| T9 | **Always-on surveillance** | Wake-word gating; no audio/frames stored by default; recording indicator; gesture mode auto-expires after 10 s idle; camera off is a hardware-honoured switch where available. |
| T10 | **Audit tampering** | Append-only table, no UPDATE/DELETE grant to the app role, hash chain (`prev_hash`), periodic external anchor. |
| T11 | **Supply chain** | Pinned deps + lockfile, hash verification, SBOM, no `curl \| sh` in setup, tool servers allowlisted, egress allowlist for agents. |
| T12 | **Sandbox escape from generated code** | Untrusted code runs in a container/jail with no network, a read-only FS except a scratch dir, CPU/memory/time caps, and a syscall filter. |
| T13 | **Model provider breach / MITM** | TLS everywhere, no secrets in prompts (T2), no PII beyond need, provider-scoped keys with rotation, fall back to local models on anomaly. |
| T14 | **Bystander disclosure** | Presence-aware output: private notifications are not spoken when another person is detected; sensitive replies route to a personal device (§66, §67). |
| T15 | **Confused deputy across devices** | A device may only act on tasks scoped to it; the action carries a permission token bound to `(task, action, device, expiry)`. |
| T16 | **False confidence** | Confidence is reported, not implied (§73); provenance is attached to important claims (§74); "success" requires a passed verification (§76). |

### T3/T4 as built (Sprint 36 · ADR 0029)

Per-device Ed25519 keys are in force. A node generates its own keypair on first run, pairs
with proof of possession plus a code a person confirms, and from then on the shared enrolment
token (ADR 0013) is closed for that machine permanently — on both sides. Revocation is
sticky, is checked before the token fallback, and removes the device from the hub. The
registry survives a restart.

Still outstanding, and named rather than implied: the private key lives in a 0600 file rather
than the OS keychain; TLS certificate pinning is not implemented; session tokens are not yet
short-lived and rotated (Sprint 41's key-rotation work).

### T7 as built (Sprint 45 · ADR 0030)

Per-task budgets were already enforced. Added: metering at the model router, so every
completion is counted rather than only the ones an agent chose to report — the reasoning and
supervision passes every turn makes were previously invisible; a daily and monthly ceiling
above any single task, checked before a paid call; and a circuit breaker that re-opens after
a cooldown instead of parking a provider until the process restarts.

Reaching a cap routes to the local model rather than refusing. A limit that becomes an outage
is a limit the owner deletes. It never lowers the bar for "done": a turn that cannot be
verified under the cap reports unverified.

Still outstanding: the ledger is in memory, so a restart resets the period totals.

### The rules as tests (Sprint 46 · ADR 0031)

`tests/integration/test_security_hardening_v46.py` asserts the absolute rules of V12 —
§90 (no secret in a prompt, memory, note or audit payload), §94 (external text is data),
§95 (one authorization point; no self-authorization; no override of ASK_ALWAYS), §102 and
§104 (external communication and every form of delete always ask), §105 (camera off, the
indicator derived from the same state the capture path checks), §110 (only the owner sets
standing behaviour), §120 (no code path fetches and runs a URL), §194 (no success without a
passed verification) — organised by section, run against the built container.

Writing them found three rules that had already stopped being true: policy resolution did not
inherit from a listed ancestor, so `file.delete.bulk` was ASK_ONCE while `file.delete` was
ASK_ALWAYS; nothing redacted a prompt on its way to a provider despite the module claiming
otherwise; and `MemoryLayer.PROCEDURAL` — the layer that shapes later work — accepted writes
from any source. All three are fixed.

### Backup and restore (Sprint 47 · ADR 0032)

`GET/POST /api/v1/backups` capture the state a fresh install cannot reconstruct — memories,
tasks, the audit chain with its hashes, the spend ledger, the owner's policy overrides and the
decision journal. The vault is excluded rather than redacted: a backup that could restore the
owner's keys hands them over when it is stolen.

Restore is destructive and behaves like it. It goes through the Permission Engine, refuses
without explicit confirmation, refuses again if the archive does not verify, and restores
nothing at all when it refuses. Policy overrides are reapplied through `override` so an edited
archive cannot auto-approve what the table always asks about, and audit entries keep their
stored hashes so `verify_chain` still catches a tampered backup.

### Software update (Sprint 48 · ADR 0033)

§120 — never execute an arbitrary update URL supplied by a model — is kept by having nowhere
to break it. `UpdateService.apply` takes a verified `Release` and its bytes, never a location;
`POST /api/v1/updates/apply` takes no body. The channel URL and the release signing key are
configuration with no setter, and an artifact must live under the configured base even when a
correctly signed manifest says otherwise: a signature over "fetch this from somewhere else" is
a valid signature.

Releases are signed over version, digest and URL together. Downgrades are refused unless asked
for, since a signed old release stays correctly signed for ever. Applying goes through the
Permission Engine as `system.update` (SYSTEM, ASK_ALWAYS), takes a backup first, and stops if
the backup fails.

This build verifies updates and cannot install one — no installer is wired, and `apply` says
so rather than reporting success. The HTTPS adapter has not been run against a real server.

### Metrics (Sprint 49 · ADR 0034)

`GET /api/v1/metrics` publishes Prometheus text. A metric label is an egress path that no
classifier sees — scraped by a system with none of Thursday's controls, retained longer, read
by whoever runs the dashboard — so label values are declared in advance and anything else
collapses to `other`. Only outcomes are labelled: decisions, verdicts, agent and action names,
redaction pattern names. Never a path, a resource or anything the owner typed.

### T13 as built (ADR 0041)

The node pins the core's SubjectPublicKeyInfo — learned at pairing, where a person is
confirming a code, and checked before the HELLO is sent. This closes a one-directional
authentication gap left by Sprint 36: the node proved who it was and the core proved nothing,
so anyone holding a certificate for the core's hostname could accept the node's HELLO and then
send it commands. A node holding a pin refuses a plaintext connection rather than falling back.

Still outstanding: short-lived, rotating session tokens.

## 14.4 Controls always on

Least privilege · explicit permission · encryption at rest (DB + vault) and in transit ·
device authentication · token rotation · sandboxing · full audit · approval for L3+ ·
**no hidden background actions** — anything Thursday did while the user was away is
listed on return.

## 14.5 Emergency stop (§69)

One call: stop all agents, cancel cancellable actions, mute mic, disable camera,
disconnect device nodes, revoke tokens, enter lockdown (read-only) until the owner
re-authenticates. It must work when the LLM is down, so it is a plain endpoint plus a
hotkey — it does not route through the reasoning engine.
