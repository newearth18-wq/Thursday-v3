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
