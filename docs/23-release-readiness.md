# 23. Release readiness

**Status: release candidate for a single-owner deployment on a trusted network. Not ready for
a multi-user or internet-exposed installation.**

That sentence is the whole document in one line. What follows is the evidence for it, and —
more usefully — the evidence against.

Written at Sprint 50 and kept current since, against 1,284 tests that need no database, no
network and no model credentials. `./scripts/check.sh` runs lint, format, types, the suite and the migrations.

---

## 23.1 What actually works

Everything below has an acceptance test that exercises it end to end through the built
container, not a unit test of the class in isolation.

| | Evidence |
|---|---|
| The vertical slice of [§15](15-vertical-slice.md) — speak, plan, act on a real machine, verify, report | `tests/e2e/test_acceptance.py` |
| One permission engine, four policies, no second door | `tests/unit/test_permissions.py`, `tests/integration/test_security_hardening_v46.py` |
| ACT → VERIFY: "done" means observed, never assumed | `tests/e2e/test_slice_*.py`, §194 tests |
| Hash-chained audit that detects tampering and deletion | `tests/unit/test_security.py` |
| Device protocol with per-device Ed25519 identity, pairing and revocation | `tests/unit/test_pairing_v36.py` |
| Voice, vision, gesture and multi-device layers | `tests/e2e/test_v4…v8_*.py` |
| Thirteen agents; skills learned, run and composed | `tests/e2e/test_v9_skills_acceptance.py` |
| Proactivity that offers rather than acts | `tests/e2e/test_v10_proactive_acceptance.py` |
| Spend metered at the router, capped, and degrading to local | `tests/e2e/test_v45_cost_acceptance.py` |
| Backup and restore, with a real round trip through a real file | `tests/integration/test_backup_v47.py` |
| Update verification with no parameter for a URL | `tests/integration/test_updates_v48.py` |
| Device key rotation, and a session that expires rather than a key that does | `tests/integration/test_rotation_v52.py` |
| Rate limits keyed on something the caller cannot forge, and a kill switch exempt from them | `tests/integration/test_rate_limits_v53.py` |
| Local AI discovered without scanning the network, and unable to download anything | `tests/integration/test_local_ai_discovery_v54.py` |
| A registry where the owner's correction outlives the node that keeps re-guessing | `tests/integration/test_model_registry_v55.py` |
| Compute routing where privacy filters rather than scores, so no weighting can outvote it | `tests/integration/test_compute_router_v56.py` |
| A fallback chain that cannot cross the privacy boundary the first choice respected | `tests/integration/test_compute_fallback_v57.py` |
| SECRET work never reaching a cloud provider, proved by a spy that records every call | `tests/integration/test_privacy_routing_v58.py` |
| One task across several machines, where no stage may be less private than the task | `tests/integration/test_distributed_ai_v59.py` |
| Waking a machine, reported only when the machine actually appears | `tests/integration/test_wake_on_lan_v60.py` |
| Measurement from real calls that cannot damn a model with one bad sample | `tests/integration/test_benchmarks_v61.py` |
| Metrics whose labels cannot carry a path or a secret | `tests/integration/test_metrics_v49.py` |

## 23.2 What is not ready, and what that would take

Named individually, because "some limitations apply" is how a gap becomes a surprise.

**Hardware-dependent layers are tested against synthetic input only.** Gesture recognition,
camera capture, microphone capture and the Windows-specific device adapters pass against
constructed landmarks, frames and audio. No camera, microphone or Windows machine has ever
run them. *To close:* run the existing acceptance tests on real hardware; the seams are
already ports, so nothing needs redesigning first.

**The OS keychain adapters have never run against a real keychain.** The port and all three
adapters exist (ADR 0040) — macOS Keychain, Windows DPAPI, Linux Secret Service — and the
node's key migrates into one when it is available, write-then-read-back-then-delete. A
configured keychain that is *not* available now fails closed instead of silently returning the
environment vault, which is what it did before. But this container is headless Linux with none
of the three, so selection, availability detection and migration ordering are tested and the
platform calls themselves are not. *To close:* one run on each of macOS, Windows and a Linux
desktop.

**Device sessions are bounded and device keys rotate; three other rotations do not.**
Certificate pinning (ADR 0041) closed the gap the pinning work itself surfaced: Sprint 36's
authentication ran in one direction, so a node proved who it was and the core proved nothing.
The node now pins the core's SubjectPublicKeyInfo, learned at pairing where a person is
present, and checks it before sending anything.

Rotation and session lifetime followed (ADR 0042), and sizing that gap found three defects
rather than one missing feature. `NodeSession.close()` dropped the session and left the
**socket open**, so revoking a connected device did not disconnect it — and §134's emergency
stop, which calls the same method to "disconnect Nodes", disconnected nothing a node could
notice. A HELLO authenticated a connection for as long as it stayed up. And
`DeviceAuthenticator` refused outright when no shared enrolment token was configured, before
it ever looked at the device's registered key, so the end state §80 aims at — every machine
paired, the enrolment token dropped because it has no job left — refused every properly
paired device.

A node now replaces its own key with `--rotate-key`, signed by both the retiring and the
incoming key, with no person at the machine; the old key stops working immediately; a revoked
device cannot rotate its way back in; and the successor is written to disk *before* the core
is asked to take it, so a lost reply is a retry rather than a physical visit. Sessions expire
at twelve hours, with no setting that removes the bound.

Key age is **reported and never enforced**, and that is a decision rather than an omission: a
device key that expired on its own would lock the owner out of their own machines on a timer.

*Still open:* the other three rotations §117 lists — the shared enrolment token, the core's
TLS key, and provider API keys.

**The updater cannot install.** It checks, verifies and refuses correctly, and no installer is
wired (ADR 0033). This is deliberate — a half-built installer is worse than none — but it
means "keep Thursday up to date" is a manual operation today.

**Persistence is partial, and the remaining part is deliberate.** Memories now survive a
restart (ADR 0036): write-through to the database, loaded at startup, with `Container.persistent`
saying whether durability is real for this deployment. Device credentials and backups are on
disk.

The **audit log** now persists too (ADR 0037): entries load in written order with their
stored hashes, the chain continues across the restart, and tampering with the stored rows is
still detected. A write that cannot be stored is never silent — `verify_chain` cannot detect
an entry that was never written, so the log marks itself degraded and health goes red.

The **spend ledger** persists as well, so a period cap binds across a restart rather than
handing back a fresh budget — the gap Sprint 45 named and Sprint 47 closed only for somebody
who had taken a backup. Pruning past the retention window reaches the table, or the rows come
back on the next start and the window never applies.

**Tasks** persist too, with the resumption story designed rather than assumed (ADR 0039). A
task never comes back `RUNNING`: it comes back `INTERRUPTED`, because the coroutine driving it
died with the process. Completed steps are done; the step that was in flight is *unknown* —
nobody observed its outcome — and whether it may be repeated is asked of the policy table, so
an interrupted `email.send` is never offered as safe. Nothing resumes itself: interrupted work
appears in the brief and at `GET /api/v1/tasks/interrupted`, and continuing is the owner's
call.

All four stores — memory, audit, spend, tasks — report their durability through `health()`.

The audit table grows without bound. A retention policy is deliberately absent: deleting audit
rows is what the append-only design forbids, and how long the owner keeps their own record is
their decision rather than a default.

**Single owner, single tenant.** There is no user model, no login, no per-user isolation.
Every control assumes one person's machine and one person's data. *This is a design position,
not an oversight* — but it means the API must not be exposed beyond localhost or a trusted
network.

**The HTTP surface is rate-limited** (ADR 0043), which closes the §128 gap this document
used to list. Four classes — anything that can reach a model is limited an order of magnitude
tighter than the rest — keyed on the peer address and never on `X-Forwarded-For`, which is a
header the caller writes and therefore a bucket the caller picks. A deployment behind the
reverse proxy §127 recommends must name that proxy in `trusted_proxies` before its header is
believed; until it does, every request behind it shares one bucket, which is a visible
degradation rather than a silent hole. §134's emergency stop is never limited, because a kill
switch an attacker can hold shut by making requests is not a kill switch.

This does not make the API safe to expose. It is single-process and in-memory, so it does not
survive a restart or coordinate across workers, and it is a bound on *rate* rather than
authentication — of which there is still none.

**Local AI compute is built and has never met a local AI.** The addendum's six sprints
(ADRs 0044–0047) plus Wake-on-LAN and benchmarks (ADR 0048) are complete and tested, and
every one of them was tested against constructed input. **No Ollama, LM Studio, llama.cpp or
vLLM instance has ever answered Thursday**; the response parsing is checked against responses
this repository wrote. This container has no inference runtime, no GPU and no second machine.
*To close:* one machine with Ollama installed and one paired node on a second machine —
the seams are ports, so nothing needs redesigning first.

Within that layer, four things are deliberately absent rather than unfinished:

- **Distributed stages run sequentially.** §21's example is naturally concurrent — vision on
  one machine while embeddings run on another — and the `needs` graph carries the information
  needed to parallelise. It waits on a per-device concurrency limit (§129), which does not
  exist; running stages in parallel without one would let a task saturate the machine it is
  running on.
- **Escalation (§13–§14) is supported, not automatic.** `ComputeExecutor` accepts a quality
  gate and walks to a stronger model when an answer fails it, and nothing in the agent layer
  passes one yet. Tier 0–5 is a policy this router can serve rather than one it runs.
- **Benchmarks do not survive a restart.** The `models` table has `tokens_per_second` and
  `last_benchmarked_at` waiting for them. Persisting needs a decision about whether a
  measurement taken before a hardware change should outlive it, and guessing that is worse
  than restarting the window.
- **Model purpose is guessed from the model's name.** No runtime reports it. The owner can
  correct a wrong guess and the correction survives reconnects (ADR 0045), but the first
  guess for an unfamiliar name is a guess.

**Waking a machine has never woken a machine.** The packet format, the policy gate, the
already-awake case and the timeout are all tested; nothing has ever gone on a real wire to a
real NIC. `_send` is injected precisely so the test suite does not broadcast magic packets
from CI at whatever machines are listening. Model cache eviction (§23) is not built at all.

**The mobile client is a scaffold.** The desktop app is built and works.

## 23.3 The security position

The rules of [V12](14-threat-model.md) that are stated as absolutes are executable tests
(ADR 0031), and writing them found three that had already stopped being true. That is the
honest summary of this project's security posture: the rules hold *and* they were checked,
recently, by something that fails when they stop holding.

What has been deliberately kept:

- Only the Permission Engine authorises. An agent cannot self-authorize; a tool, a document
  and a model cannot change policy.
- External content is data. A page, a file, an OCR result or a backup cannot widen what
  Thursday may do — including a backup that has been edited to auto-approve deletion.
- Secrets never reach a prompt, memory, a note, the audit log, a metric label or a backup.
- Every form of delete and every external communication asks, every time.
- A repair may restore a capability and never widen one.
- A cap, a failure or an outage never lowers the bar for "done".

What is *not* claimed: resistance to a determined attacker with local code execution, a
compromised release signing key, or a malicious owner. Those are outside the model.

## 23.4 How to judge this yourself

```bash
./scripts/check.sh            # everything CI runs
pytest tests/e2e -q           # the acceptance tests, which are the interesting ones
pytest tests/integration/test_security_hardening_v46.py -q   # the V12 rules
python -m apps.cli --device-name Office-PC                   # talk to it
```

`tests/integration/test_release_readiness_v50.py` re-checks the claims in this document
mechanically: that everything the container declares is built, that every action has a policy
and every agent a contract, that every ADR is indexed and every internal link resolves, and
that the README's counts have not fallen behind. It cannot prove Thursday is good. It proves
the documentation is not lying, which is the part that decays silently and the part a reader
has no way to check for themselves.
