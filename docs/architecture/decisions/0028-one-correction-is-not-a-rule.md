# ADR 0028 — One correction is not a rule, and a repair is not a permission

**Status:** accepted · **Date:** 2026-09-02

## Context

Two V10 subsystems learn or heal without being asked, and both have an obvious shortcut that
is wrong in the same way: it lets the system quietly change what it is, without anybody
deciding that it should.

**Feedback.** The owner says "แบบนี้ไม่เอา". Writing that down as a preference is trivial and
feels responsive. But a single "no" is ambiguous in a way a stored preference is not — it
might mean *never do this*, or *not for this document*, or *not today*, or *you
misunderstood me entirely*. Storing the strongest reading of an ambiguous signal produces an
assistant that progressively stops doing things for reasons nobody remembers, and the owner
cannot find the rule to change it because nothing announced itself. The spec says so
directly: *"ห้ามเปลี่ยน permanent preference จากเหตุการณ์เดียวโดยไม่มี confidence"*.

**Self-recovery.** Something is broken and the fix is obvious. V10 lists what may be repaired
automatically — restart a worker, retry a request, switch model, switch agent, reconnect a
node — and what may not: change security, install a component, disable protection, admin
repair.

## Decision

**A correction is an event, not a preference.** `FeedbackLog.record` stores a
`FeedbackEvent` and changes nothing. Only a subject corrected `CONFIDENCE_REPEATS` times,
within `FEEDBACK_WINDOW`, becomes a `PreferenceProposal` — and even then it is a *question*,
because an agent may not write the owner's preferences (PART 76). Old corrections stop
counting: a complaint about a format nobody uses any more should not still be accumulating
towards a rule.

**Self-evaluation reads the record, never a model.** Did it work, was it verified, how many
attempts, did the owner rewrite the result. A system that scores its own work by asking a
model how it did will report that it did well. Agent scores are kept as a *record, not a
ranking* — an agent handling hard jobs scores below one handling easy jobs, and reading it as
"which agent is best" would route work away from the agent that takes the difficult cases.

**A repair may restore a capability, never widen one.** That is the line between V10's two
lists, and it is why they divide where they do. Every allowed repair returns the system to
something it could already do; every forbidden one changes what it is *permitted* to do. A
system that can widen its own permissions in order to fix itself has no permission model,
only a delay before it decides it needs more.

`SelfRecovery.register` refuses a forbidden repair **at wiring time**, not at call time: a
forbidden repair that exists and merely is never invoked is one line away from being invoked,
and the wiring is where a reviewer would look. The deny list is checked first and an unlisted
action is refused, so a repair nobody thought about is refused rather than allowed.

**A recovery that loops is an outage.** Attempts are bounded per component; exhausting them
escalates, because at that point Thursday genuinely cannot fix this and continuing to try
hides the failure from the one person who could. The counter resets after a quiet period —
otherwise a system up for a month used its three attempts in March and can never self-heal
again.

## Consequences

- Thursday takes longer to learn a preference than it could. The alternative is a system
  whose behaviour drifts for reasons no one can reconstruct, which is worse at any speed.
- Exhausted components appear in the morning brief, so "Thursday gave up on this" is
  something the owner reads rather than something they discover.
- **Cost we accepted:** a genuinely one-off correction ("never do that again, ever") takes
  three occurrences or an explicit instruction. The explicit instruction already works — it
  is `MEMORY_WRITE` (ADR 0018), and it is the right way to say something once and mean it.

## Alternatives considered

- **Weight a strongly-worded correction higher.** Rejected: the strength of the wording
  measures irritation, not generality, and irritation is highest exactly when the owner is
  least likely to be describing a permanent rule.
- **Let recovery escalate its own permissions temporarily.** Rejected — see above; a
  temporary widening is a widening, and the temporary part is a promise made by the code
  that needed it.
