# 23. Release readiness

**Status: release candidate for a single-owner deployment on a trusted network. Not ready for
a multi-user or internet-exposed installation.**

That sentence is the whole document in one line. What follows is the evidence for it, and —
more usefully — the evidence against.

Written at Sprint 50 and kept current since, against 2,473 tests that need no database, no
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
| Eighteen agents; skills learned, run and composed | `tests/e2e/test_v9_skills_acceptance.py` |
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
| A desktop install that needs no database server, no Redis and no Docker | `tests/integration/test_desktop_edition_v62.py` |
| A hardware recommendation where PRIVATE can never quietly become cloud | `tests/integration/test_recommendation_v63.py` |
| A first run that cannot finish until Thursday has actually opened something | `tests/integration/test_setup_wizard_v64.py` |
| Errors and activity in plain language, from a declared list rather than a filter | `tests/integration/test_plain_language_v65.py` |
| A Repair button that cannot offer a repair the Permission Engine would refuse, and reports what the machine shows rather than what the handler returned | `tests/integration/test_checkup_v66.py` |
| A tutor whose lessons end when the machine proves it, and whose practice mode has no execution path | `tests/e2e/test_onboarding_acceptance.py` |
| A photograph, a replayed video and a recorded voice each refused with the matcher reporting a perfect score | `tests/e2e/test_identity_acceptance.py` |
| Metrics whose labels cannot carry a path or a secret | `tests/integration/test_metrics_v49.py` |
| A promotional video assembled, narrated, subtitled and rendered, then judged by opening the file | `tests/e2e/test_v11_media_acceptance.py` |
| Every editing operation against a real ffmpeg, each asserted on a probe of its output | `tests/integration/test_media_editing_v11.py` |
| A rubric, a blueprint and a lesson plan whose arithmetic the Supervisor recomputes for itself | `tests/integration/test_teacher_agent_v13.py` |
| Circulation and collection counted from records, with overdue answered as at a stated date | `tests/integration/test_library_agent_v13.py` |
| A run sheet whose clock times are computed, and the person rostered onto two consecutive items | `tests/integration/test_event_agent_v13.py` |
| A script spoken by Thursday, timed off the real audio, rendered, and the finished video's soundtrack checked for being audible | `tests/e2e/test_v12_narration_acceptance.py` |
| A backtest whose trade count the Supervisor recomputes, an order type no module outside `risk.py` can construct, and a live stage with no adapter behind it | `tests/integration/test_trading_agent_v14.py` |
| A workflow saved disabled whatever the request said, with each action's permission decision resolved before it is armed | `tests/integration/test_workflow_api_v15.py` |
| Schedule triggers that fire — five-field cron in the owner's timezone, swept by the worker | `tests/unit/test_schedule_sweep_v15.py` |
| An unauthenticated caller who cannot open an app on the owner's machine, on the HTTP surface and on the socket that bypasses its middleware | `tests/integration/test_api_auth_v25.py` |
| A core whose TLS key changes while nobody walks to a machine, followed over a real handshake against a certificate that really was replaced | `tests/integration/test_tls_rotation_live_v23.py` |

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

**Every credential in the system now rotates, and the last one to arrive is the one that
looked impossible.**
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

The **shared enrolment token** now rotates too (ADR 0066), and the reason it took until
Sprint 97 is worth recording: the rotation was not hard to implement, it was hard to
implement honestly. Changing the variable and restarting refuses every un-paired node
mid-enrolment with a message that reads as a typo, and tells nobody whether the old secret is
still in use. So a rotation is a **window with a stated end**: both tokens are accepted until
`retires_at`, every acceptance names by fingerprint which token let the node in, a node that
arrives late is told the date the old one stopped working, and a half-configured rotation
fails at startup rather than at the first HELLO months later. Nothing generates a secret —
there is no token generator, and a test asserts there is none.

**Provider API keys** rotate too (ADR 0067), in the opposite shape to the enrolment token.
One party holds the key, so replacing it cuts nothing off and needs no window; the danger is
entirely that the replacement does not work — copied short, pasted with a newline, revoked
before it was installed, or from a different account. So the incoming key is proven against
the provider with a **real call** before anything is written, the outgoing key is kept rather
than deleted so there is a way back, and every rotation carries the sentence naming what
Thursday cannot do: **revoke the old key at the provider.** That is a button in the provider's
console and nothing here can press it, so a rotation reporting success without saying so
would leave the owner believing a compromised key was dead.

`AnthropicLLM.health()` stopped reporting `"ok"` for a registered key in the same sprint — the
old wording read as "the provider is working" and was true of a revoked one.

The **core's TLS key** was the one left, and it took a different shape from the other three
(ADR 0071). A window was no use — a node switched off for it is still stranded — and a verified
swap was no use either, because the party that has to be convinced is the node, not the core.

What made it possible is that a pin is `sha256(SubjectPublicKeyInfo)`, which is a *commitment to
a public key*. A statement carrying the retiring SPKI can therefore be checked against nothing
but the pin, and once the hash matches, the node holds the real key and can verify a signature
made by its private half. So the retiring key can name its successor and every node can check
the claim without a new anchor and without re-pairing. Following it grants no power the retiring
key did not already have, which is what makes it sound: a holder of that key could impersonate
the core to a node pinned to it anyway.

There is **no window**, and that is the part that matters for machines nobody is watching. The
statement is a permanent fact rather than a permission, so a node follows it the first time it
fails to connect — a laptop in a bag for six months and two rotations walks the chain when it
comes back out. It asks once per pin rather than once per reconnection, because a node whose pin
genuinely does not match would otherwise hammer its own core forever.

Two refusals are worth naming, because they are the honest half of it. **Thursday does not hold
the core's TLS key** — it does not generate, store, install or rotate it, since the core is
served behind whatever terminates TLS for it; the signing tool is run by hand on the host by
whoever holds the key, and it touches no network. And **a compromised key cannot be handed
over**: whoever else holds it can sign a hand-over too, so each node follows whichever reaches
it first, and rotating away from a stolen key is a race rather than a fix. `--compromised`
refuses and writes nothing, and names the only thing that removes the old key's authority
instead of racing it — re-pairing each node with a person at the machine.

That gap is now closed, and closing it found something. The test serves the real application
under uvicorn over TLS, stops it, and starts it again on the same port with a certificate on a
different key — which is what a rotation *is* — and then lets the node's own `run_forever` meet
it with nothing replaced. The node fails the pin check on a real handshake, fetches the chain
over a real request to the host it cannot yet trust, re-pins, and reconnects.

What that found is unrelated to hand-overs and was waiting for anybody who looked: **a core
that refused the HELLO ended the node process.** The refusal left `run_forever` as a bare
`RuntimeError`, and `main` gathers that loop with the node's diagnostics server — so the whole
process went down, including `GET /health`, whose only job is to report `last_error`. The
refusal destroyed the explanation for the refusal, and a pairing code nobody had confirmed yet
was enough to do it. It now backs off like every other reason a session ended, which is what
this repository's own rule for close codes already said. A genuine defect inside a session
still crashes loudly, which is why the refusal got its own exception type rather than a caught
`RuntimeError`.

**Thursday can speak, and it sounds like a machine.** eSpeak NG (ADR 0061) is a real local
synthesiser that installs as a wheel with no model file, covers Thai, and produces audio
whose measured durations time the subtitles the media pipeline burns in — which is what
closed V11's estimated-timing gap. What it is not is pleasant to listen to: it is formant
synthesis, and for a video somebody will show at a school that matters. *To close:* a Piper
voice file, which the existing chain already prefers where one is present — a download
rather than a design change.

Still true of the whole voice layer: **no microphone or speaker has ever been opened.**
Narration writes files.

**Media editing is built; media *generation* is not.** Editing is real, local and
deterministic (ADR 0060) — trim, join, resize, subtitle, dub, normalise, overlay, crossfade —
and every operation is tested against a real ffmpeg with the assertion made on a probe of the
output rather than on an exit code. It needs ffmpeg on the machine: the container discovers
one at startup, and a machine without one gets an editor that refuses every call with the
remedy in the sentence while `health()` reports `media` as unavailable. That is a supported
deployment, not a degraded one.

What is missing is the *picture* side of creation: there is no text-to-image and no
text-to-video, so `creative.compose` takes storyboard frames as an input and returns
`ready=False` naming what it lacks. The brief's creative workflow is therefore half-built on
purpose — the deterministic half. *To close:* a provider behind a port for each, in the shape
ADR 0001 describes.

This paragraph said "and no text-to-speech" until Sprint 96, twenty lines below the one that
describes Thursday's voice. V12 added the synthesiser and nobody came back to this sentence,
so the document whose whole job is to be honest about gaps was claiming a gap that had been
closed. `test_the_readiness_document_does_not_deny_a_capability_thursday_has` now checks it.

Two smaller limits, both deliberate. **Silence removal refuses on video**: cutting silence
from a soundtrack while leaving the picture alone desynchronises the two for the rest of the
video, so it works on audio, where it is actually wanted, and says why it will not do the
rest. And **subtitle burn-in depends on the machine's fonts** — libass renders what
fontconfig can find, so a system with no Thai font produces boxes. The quality gate cannot
see that, and this document says so rather than letting the test suite's green imply
otherwise.

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

**The API now knows whether the caller is the owner** (ADR 0073), which is the half that
sentence used to end on. The hole was measured rather than argued about: an unauthenticated
`POST /devices/{id}/actions` with `app.open` came back `200, ok, verified` and Chrome opened on
the owner's machine. The device channel demands an Ed25519 signature from every node before it
carries a frame; the HTTP API beside it ran the same catalogue for anybody who could reach the
port.

One owner, so one token — `Authorization: Bearer`, compared with `compare_digest`, and nothing
here generates it. **Not configured means loopback only**, which turns this document's own
"must not be exposed beyond localhost" from a warning into a control, and leaves the ordinary
install needing no ceremony. **And loopback is not a credential:** a page in the owner's browser
can be made to arrive on 127.0.0.1 by resolving its own hostname there, so loopback-only mode
requires a loopback `Host` too.

The WebSocket was nearly a way round all of it — `@app.middleware("http")` does not run for one,
and `/realtime` takes a turn straight into the reasoning engine. It calls the same `admit` the
middleware does; a browser cannot set a header on a WebSocket, so the token may arrive as a
subprotocol instead.

*Breaking:* a deployment reachable from another machine now needs a token, and a trusted proxy
in front of a tokenless one fails at startup rather than making the whole internet look like
the owner at the keyboard.

*Still open:* the rate limiter itself is single-process and in-memory, so it does not survive a
restart or coordinate across workers. And a token on a plaintext network is a token anybody on
that network can read — the device channel is pinned (ADR 0041), and terminating TLS for the
HTTP surface is the operator's job.

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

**"Repair Thursday" now repairs one thing, and the count is the finding** (ADR 0072). This
document asked for three real handlers to replace the placeholders left from V10. Building
them established that **two of the three could never have worked**, which is a better outcome
than three handlers would have been.

`reconnect_node` cannot exist: a node **dials the core**, so there is no address to dial back
and no way to start a process on somebody else's machine — and the `devices` check is
all-or-nothing, unhealthy only when nothing at all is connected, so there is not even a stale
session to close. `restart_worker` cannot either: the background worker is a separate process
with its own container, and starting a process the core does not own is the neighbourhood of
"install a system component", which is on the never-automatic list for good reasons.

`switch_model` can, because the router owns provider selection in this process. Pressing
Repair parks the failing provider immediately rather than waiting for the breaker's three
consecutive failures — the same park, reached by a different route, because the owner reading
a health check *is* the evidence the breaker was waiting for. It will not park the last
provider that can still be chosen: a repair that leaves `choose` raising `ProviderError` at
every request has made things worse than the failure it was called to fix.

Two things changed around it. **A button is offered only where a repair is both permitted and
wired to something** — the old predicate asked only whether an action was *allowed*, so a
permitted repair nobody had implemented appeared as a button that, when pressed, replied that
there is no automatic repair for this part. And **a repair is verified against what it
restores, not against what broke**: switching models leaves the failing provider failing, so
re-checking that provider would have reported every successful switch as a failure.

Where a button was removed, the sentence saying what a person has to do is shown in its place,
in normal mode rather than in Developer Options — which is the whole point of not offering a
control that cannot work.

*Still open:* nothing else is repairable from the core, and that is the position rather than a
gap. A future repair earns a button by being wired to something that works, not by being on
the permitted list.

**No face or voice has ever been recognised.** Sprints 73-79 built the identity layer:
the secure template store, enrolment, liveness, the fusion engine, presence, the gate and
recovery. **This container has no camera, no microphone, no Windows Hello and no depth
sensor**, so every frame and audio sample in the tests is a Python object and every provider
returns a number a test chose. What is proved is that the *policy* composes — a perfect match
with no liveness is refused all the way to the gate, a locked session cannot be inherited, a
refusal says nothing. What is not proved, at all, is whether a real recogniser distinguishes
two people.

That gap is why `NoFaceRecognition` and `NoSpeakerRecognition` authenticate nobody rather than
returning a plausible number: a stub that did would be a lock that opens for everybody while
reporting itself locked. **Shipping this with biometrics enabled requires installing a real
provider**; a deployment that has not, has not. *To close:* a machine with a camera and a
microphone, a real recogniser behind the two ports, and the §52 anti-spoof battery run against
an actual printed photograph, an actual phone screen and an actual recording.

**The Learning Center is rendered; two of its lessons are not.** Sprints 67-72 built the
tutor as API and logic and nothing drew it, so the learning path, the suggestion engine and
the lessons with real verification existed and no owner could reach them. Sprint 96 added the
view: the path by stage, the one suggested next thing with its reason, the practice offers
with their real policy decisions, and lessons that start, check and skip.

It has **no control that marks a lesson complete**, and that is the property worth keeping:
`done` arrives from the server, `/attempt` takes evidence rather than a verdict, and a step's
own check reads the machine. A "mark as done" button would turn the screen from a record of
what the owner can do into a record of what they clicked.

The **§13 walkthrough** landed in Sprint 99 (ADR 0068). A lesson names a control rather than
describing where it is; the desktop marks its controls and the interface finds the real one
at the moment it draws, so no coordinate is stored anywhere. Two properties matter more than
the arrow itself: a name a lesson uses **must exist in the app**, enforced by a test that
scans the desktop source — renaming a control and leaving the lesson behind fails the suite —
and a control that is not on screen gets **no arrow and a sentence saying so**, because an
arrow is a claim about where something is and a wrong one costs the next one too. It points;
it does not press.

*Still open:* §22's gesture tutorial with a live hand skeleton. It needs a camera, and no
camera has ever run against this code.

**Some lessons the spec names are not written.** §17's five basics are, plus stopping. The
vision (§21), gesture (§22), agent (§19), automation (§20) and multi-device (§64) lessons are
not — each needs hardware or a second machine this container has never had, and writing a
lesson that has never been run against the thing it teaches is how the camera lesson would
come to describe a camera nobody tested.

**The phone shows status and answers approvals; everything else §64 lists is not built.**
"Scaffold" was generous until Sprint 100: `apps/mobile/` held one README describing a Flutter
app nobody was building, while the real Android client shipped from `apps/desktop` — CI has
cross-compiled an `.apk` on every commit since Sprint 87 (ADR 0057). What it lacked was a
layout, so at phone width the owner got a desktop window: a nav pinned to the bottom-left, a
21rem drawer over a 900-unit canvas.

There is now a phone layout with two things on it, and one rule that is not about layout:
**a phone may approve, but may not grant standing permission** (ADR 0069). It is enforced in
the client and stated as such — the core cannot tell which surface a request came from without
trusting a header the client sets, and a header the client sets is not a security control. It
protects against the owner in a hurry, not against a stolen phone; for that, revoke the device.

**Device control landed in Sprint 101 (ADR 0070), and closing it found something bigger.**
§20's own headline scenario did not work from *anywhere*: `POST /devices/{id}/actions` answered
anything the engine did not call AUTO with 403, so "ปิดเครื่องให้หน่อย" (`system.power`,
ASK_ALWAYS) was refused rather than asked about — and so was locking a screen, which is
ASK_ONCE, LOW and reversible. Eight of the catalogue's thirty actions were unreachable that
way, for every caller. The endpoint now raises the approval and returns 202; BLOCK is still
403, because that is the engine saying no rather than asking.

From a phone the rule is narrower and the criterion is *can the surface that took the action
undo it?* Lock and wake, yes — both recoverable. Sleep, restart and shut down, no: the owner is
in another building, unsaved work is gone immediately, and the apparent undo is wake-on-LAN,
which this document says has never actually woken a machine. It is an allowlist, so a verb
added to the catalogue later is off the phone until somebody decides otherwise.

*Still open:* voice remote, conversation, camera input and push notifications. Each needs its
own decision about what a phone is allowed to do; ADR 0070 is the shape those decisions should
take rather than a precedent for assuming them.

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
