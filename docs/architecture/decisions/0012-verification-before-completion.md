# ADR 0012 — A task completes only on evidence, never on the absence of an error

**Status:** accepted · **Date:** 2026-09-02

## Context

`subprocess.Popen("chrome")` returns a process handle. It returns one when Chrome starts,
and it returns one when Chrome's installer is corrupt and the process exits immediately. An
exception was not raised in either case.

This is the failure the whole system is designed around, because it is the one that
destroys trust fastest. An assistant that is wrong is annoying. An assistant that is
*confidently* wrong — "I opened Chrome" when nothing opened — teaches its owner to verify
everything it says, at which point it has negative value.

## Decision

`verified` derives from an observation made *after* the action, never from the action's own
return value.

- The node acts, then looks: `app.open` polls the process table and reads the window title.
  `file.write` reads the bytes back and hashes them. `browser.type` reads the field back.
- The agent observes independently, before and after, so "it changed" is a comparison
  rather than a claim.
- The Supervisor is read-only and reaches its own verdict from the evidence.
- `TaskManager.complete()` raises unless a verification passed. The state machine has no
  transition from `RUNNING` to `COMPLETED` — everything routes through `VERIFYING`.

An action that ran but could not be confirmed is reported as **unverified**, which is a
third outcome, distinct from success and from failure. The owner is told what was
dispatched and what could not be confirmed, and the task does not complete.

## Consequences

- Every device action costs at least one extra round trip. Accepted without argument.
- Tools must define what evidence looks like for them. A tool that cannot be verified must
  say so, and its results are marked unverified rather than assumed.
- `FakeDeviceNode(fail_launch=True)` exists precisely to produce the dishonest case on
  demand: the command succeeds, nothing starts. A test double that can only succeed cannot
  test the property the system is built on.
- **Cost we accepted:** latency, and occasional false negatives where the effect was real
  but the evidence was not visible. Reporting "I could not confirm it" when something did
  work is a far cheaper mistake than the reverse.

## Alternatives considered

- **Trust the exit code.** Rejected: it is exactly the signal that lies in the case that
  matters.
- **Ask the model whether it worked.** Rejected: the model has no access to the machine's
  state, so it can only produce a plausible guess — the same failure with more words.
- **Optimistic completion with later correction.** Rejected: by the time a correction
  arrives the owner has already acted on the first answer.
