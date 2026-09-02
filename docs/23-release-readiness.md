# 23. Release readiness

**Status: release candidate for a single-owner deployment on a trusted network. Not ready for
a multi-user or internet-exposed installation.**

That sentence is the whole document in one line. What follows is the evidence for it, and —
more usefully — the evidence against.

Written at Sprint 50, against 986 tests that need no database, no network and no model
credentials. `./scripts/check.sh` runs lint, format, types, the suite and the migrations.

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
| Metrics whose labels cannot carry a path or a secret | `tests/integration/test_metrics_v49.py` |

## 23.2 What is not ready, and what that would take

Named individually, because "some limitations apply" is how a gap becomes a surprise.

**Hardware-dependent layers are tested against synthetic input only.** Gesture recognition,
camera capture, microphone capture and the Windows-specific device adapters pass against
constructed landmarks, frames and audio. No camera, microphone or Windows machine has ever
run them. *To close:* run the existing acceptance tests on real hardware; the seams are
already ports, so nothing needs redesigning first.

**The node's private key is a 0600 file, not the OS keychain.** Pairing, revocation and
signature checking are real (ADR 0029); the storage is the weak part. *To close:* a Keychain /
DPAPI / Secret Service adapter behind the existing `PrivateKey` interface.

**No TLS certificate pinning, and no session-token rotation.** The device protocol
authenticates the node; it does not defend against a compromised CA. *To close:* Sprint 41's
key-rotation work.

**The updater cannot install.** It checks, verifies and refuses correctly, and no installer is
wired (ADR 0033). This is deliberate — a half-built installer is worse than none — but it
means "keep Thursday up to date" is a manual operation today.

**Persistence is partial.** Device credentials and backups are on disk. Memory, tasks, the
audit log and the spend ledger live in the running process and survive a restart only via a
backup somebody took. The Postgres schema and migrations exist and are checked in CI; the
services do not yet read through them. *To close:* repository implementations behind the
existing manager interfaces.

**Single owner, single tenant.** There is no user model, no login, no per-user isolation.
Every control assumes one person's machine and one person's data. *This is a design position,
not an oversight* — but it means the API must not be exposed beyond localhost or a trusted
network.

**No rate limiting on the HTTP surface.** Consistent with the above: it assumes a local
listener. *To close before any exposure.*

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
