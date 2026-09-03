# ADR 0039 — An interrupted step is unknown, not failed

**Status:** accepted · **Date:** 2026-09-03 · **Completes:** [ADR 0036](0036-the-table-is-the-truth-the-dict-is-an-index.md)

## Context

Tasks were the one store deliberately left in memory, twice, on the grounds that persisting
them without a resumption story would be worse than losing them. That was the right call and
this ADR is the story.

The problem is not storage. A `RUNNING` row reloaded as `RUNNING` describes a task that looks
alive with nothing driving it: the coroutine executing it died with the process. The owner
watches work in progress that will never progress, and has no reason to think anything is
wrong. That is strictly worse than the task vanishing, because vanishing is at least visible.

## Decision

**A task loaded from storage never comes back `RUNNING`.** It comes back `INTERRUPTED`, a new
state meaning "this was running when the process stopped, and nothing is driving it now". It
is entered only by the loader — a live process moving a task there would be claiming a crash
that did not happen.

`INTERRUPTED` rather than reusing `PAUSED`, because the difference is the whole point: "you
stopped this" and "we crashed while doing this" call for different responses, and only the
second leaves a step whose outcome nobody observed.

**What is known about the plan comes from the system's own principles.**

| step status | what is known | why |
|---|---|---|
| completed | done | its outcome was observed (ADR 0012) |
| running | **unknown** | nobody watched it finish, so nobody can say it did |
| pending | not started | safe |

The unknown step decides everything. It may have completed, half-completed, or never started.
Calling it *failed* is a claim nobody can support; calling it *done* is worse.

**Whether the unknown step may be repeated is asked of the policy table, not answered here.**
Read-only, reversible and local is safe to repeat. `email.send` and `message.send` reach
outside this machine and may already have happened — §194 forbids silently duplicating an
external communication, and "probably it did not send" is not a basis for sending a second
one. `file.delete` cannot be undone and a repeat may take the restored copy.

Asking the table rather than keeping a list of dangerous action names here is deliberate: a
second list is a second thing to keep in step, and this repository has found that bug enough
times to stop writing them. There is a test asserting the rule contains no hardcoded action
names.

**Nothing resumes itself.** `resumption.py` reports; the module has no method that runs
anything, and there is a test asserting that too. Two reasons: the unknown step's safety is a
judgement about the *owner's* data, and a process that auto-resumed on boot would, in a crash
loop, redo the same dangerous thing on every restart. Interrupted work appears in the morning
brief's `issues` — not `suggestions`, because it is not an offer — and at
`GET /api/v1/tasks/interrupted`.

**Every public mutator persists, and a test enumerates them.** Five methods mutate a task and
most funnel through `transition`. "Remember to call `_save`" is how the sixth one silently
does not, so the test walks `TaskManager`'s public API and checks each mutator either saves or
delegates to one that does.

## Consequences

- Work interrupted by a crash is visible, explained, and continuable — and the continuation
  decision is put to the owner with the specific reason it needs them.
- **Two schema mismatches closed:** `tasks.plan` and `tasks.verification` were `NOT NULL`
  while the domain model expresses "no plan yet" as `None`. Storing `{}` instead would have
  round-tripped as an *empty plan*, which is a different claim: one says nobody has planned
  this, the other says somebody planned it and it needs no steps. Migration `26294a50a735`.
- `TaskManager.charge` is async, since it mutates and must persist. Two production call
  sites, both already async.
- **Cost we accepted:** a task interrupted mid-step stays interrupted until somebody looks.
  For a personal assistant that is right — the alternative is a machine that quietly redoes
  things while you are asleep.

## Alternatives considered

- **Reload as `RUNNING` and let the orchestrator notice.** Rejected: nothing is watching, so
  "notice" means never, and in the meantime the state is a lie.
- **Mark the in-flight step failed and retry it.** Rejected: failed is a claim about an
  outcome nobody observed, and retry-by-default is exactly what duplicates an email.
- **Auto-resume when every remaining step is safe.** Tempting, and rejected: the *first*
  remaining step is the unknown one, so "every remaining step is safe" is a judgement about
  the step we know least about. Offering it costs one interaction; getting it wrong costs a
  duplicate purchase.
- **Keep tasks in memory and rely on the backup.** Rejected — that is where this started, and
  it makes durability depend on somebody having thought to take one.
