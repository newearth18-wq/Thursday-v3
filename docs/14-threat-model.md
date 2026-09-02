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
