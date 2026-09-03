# Architecture Decision Records

One file per decision that would be expensive to reverse. Format: context, decision,
consequences — including the consequences we did not like and accepted anyway.

A decision is worth an ADR when a future reader would otherwise ask "why on earth is it
done this way?" and have no answer.

| # | Decision | The short version |
|---|---|---|
| [0001](0001-ports-and-adapters.md) | Ports and adapters, one DI container | Every port has a real adapter and an offline one |
| [0002](0002-postgres-pgvector-single-store.md) | Postgres + pgvector, no separate vector DB | One store, one transaction, one backup |
| [0003](0003-dramatiq-over-celery.md) | Dramatiq over Celery | Far fewer moving parts for the same job |
| [0004](0004-orchestrator-in-house.md) | No vendor agent framework in the core | The supervise/permit/verify loop *is* the product |
| [0005](0005-monorepo-package-split.md) | One repo, many packages | Import boundaries without release coordination |
| [0006](0006-offline-defaults-for-tests.md) | Tests need no infrastructure | Safety properties testable only in production are untested |
| [0007](0007-namespaced-node-commands.md) | Namespaced actions, prefix resolution | A new `audit.*` verb is blocked without anyone remembering |
| [0008](0008-ask-once-vs-ask-always.md) | `ASK_ONCE` ≠ `ASK_ALWAYS` | No sequence of hurried approvals creates a standing one |
| [0009](0009-autonomy-separate-from-proactivity.md) | Acting and speaking are separate dials | Raising one never raises the other |
| [0010](0010-untrusted-content-is-data.md) | Page and file content is data, never instruction | A web page cannot widen what Thursday may do |
| [0011](0011-permission-before-execution.md) | One engine, on the only execution path | A caller cannot skip the check by forgetting it |
| [0012](0012-verification-before-completion.md) | Completion requires evidence | `Popen` returns a handle whether or not the app started |
| [0013](0013-device-token-bootstrap-auth.md) | Signed HELLO, actually verified | An impostor node could report success it never achieved |
| [0014](0014-device-node-separation.md) | The core touches no OS | Confinement lives where the machine is, not where the model is |
| [0015](0015-outbound-websocket-transport.md) | Node dials out; diagnostics are separate | A personal laptop cannot accept inbound connections |
| [0016](0016-voice-state-machine.md) | The voice loop is a state machine | A boolean cannot express barge-in, so barge-in did not work |
| [0017](0017-voice-provider-fallback.md) | Speech providers are an ordered chain | The network fails mid-utterance, not at startup |
| [0018](0018-memory-that-changes-behaviour.md) | Remembered instructions are applied, not just recalled | A memory that changes nothing is a note |
| [0019](0019-forgetting-is-a-first-class-operation.md) | Forgetting is an instruction | A memory wrongly kept is a failure the owner cannot see |
| [0020](0020-the-camera-is-off.md) | The camera is off, provably | An indicator computed separately can disagree with reality |
| [0021](0021-a-stream-never-leaves-the-machine.md) | A video stream never leaves the machine | 108,000 frames an hour is not something anyone can consent to |
| [0022](0022-a-gesture-is-not-a-signature.md) | A gesture is not a signature | It is inferred, not performed — so it may never confirm anything consequential |
| [0023](0023-the-conversation-knows-which-machine.md) | The conversation knows which machine, and says so | A command that succeeds on the wrong machine is reported as success |
| [0024](0024-a-command-that-crosses-machines.md) | A command that crosses machines is a different command | Distance removes the owner's ability to notice and intervene |
| [0025](0025-a-figure-in-a-report-was-computed.md) | Every figure in a report was computed | A plausible wrong number survives every check except the one nobody ran |
| [0026](0026-a-learned-skill-is-code-nobody-reviewed.md) | A learned skill is code nobody reviewed | Running it must not become a way around the rules everything else obeys |
| [0027](0027-noticing-is-not-doing.md) | Noticing is not doing | Proactive must not become a system that does whatever it likes |
| [0028](0028-one-correction-is-not-a-rule.md) | One correction is not a rule, and a repair is not a permission | Both let the system quietly change what it is |
| [0029](0029-a-paired-device-leaves-the-shared-token-behind.md) | A paired device leaves the shared token behind | A secret that authenticates *a* node authenticates every impostor too |
| [0030](0030-a-budget-that-stops-thursday-is-worse-than-the-overspend.md) | A spending cap degrades; it does not stop | A limit that becomes an outage is a limit the owner deletes |
| [0031](0031-a-security-rule-nobody-tests-is-a-hope.md) | The absolute security rules are executable tests | A property asserted only in prose stops being true without failing |
| [0032](0032-a-backup-nobody-has-restored-is-a-hope.md) | A backup nobody has restored is a hope | The value is entirely in the restore path, used once, under pressure |
| [0033](0033-the-updater-has-no-parameter-for-a-url.md) | The updater has no parameter for a URL | A rule enforced by a check is one somebody can forget to call |
| [0034](0034-a-metric-label-is-an-egress-path.md) | A metric label is an egress path nobody classifies | Monitoring has none of Thursday's privacy controls and keeps data longer |
| [0035](0035-the-release-gate-is-a-test-not-a-checklist.md) | The release gate is a test, not a checklist | Nine documented-but-untrue claims, none of which failed a test |
| [0036](0036-the-table-is-the-truth-the-dict-is-an-index.md) | The table is the truth, the dict is an index | Two stores that can disagree are worse than one store and no persistence |
| [0037](0037-a-missing-audit-entry-leaves-a-valid-chain.md) | A missing audit entry leaves a valid chain | The one failure the hash chain cannot detect, so it must never be silent |
| [0038](0038-a-lost-charge-makes-the-cap-under-bind.md) | A lost charge makes the cap under-bind | A lost record is an accountability failure; a lost constraint spends money |
| [0039](0039-an-interrupted-step-is-unknown-not-failed.md) | An interrupted step is unknown, not failed | Nobody watched it finish, so nobody can say it did — or that it did not |
| [0040](0040-an-imagined-protection-is-worse-than-a-known-weakness.md) | An imagined protection is worse than a known weakness | A configured keychain that silently returned the environment vault |
| [0041](0041-the-node-authenticates-the-core-too.md) | The node authenticates the core, too | Pairing proved the node to the core and the core to nobody |
