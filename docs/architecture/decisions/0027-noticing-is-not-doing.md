# ADR 0027 — Noticing is not doing

**Status:** accepted · **Date:** 2026-09-02

## Context

V10 is where Thursday stops being a thing you talk to and starts being a thing that talks to
you. The spec frames the risk in its own goal statement: *"Thursday เริ่มทำหน้าที่เป็นผู้ช่วย
เชิงรุก แต่ห้ามกลายเป็น autonomous system ที่ทำอะไรก็ได้เอง"* — proactive, and not a system
that does whatever it likes.

The failure this prevents is not a dramatic one. It is a system that helpfully drafts a
document nobody asked for, sends a polite email to confirm a meeting, tidies a folder it
judged messy — each individually defensible, each a thing the owner did not know was going
to happen, and collectively an assistant nobody can predict. Predictability is most of what
makes a tool trustworthy, and a proactive system spends it faster than any other kind.

There is a second failure that looks smaller and is not. An assistant that is right nine
times in ten and speaks forty times a day gets turned off, and one that has been turned off
has a safety record of zero. Volume is a safety property here, not a UX preference.

## Decision

**An observation is not an action.** `ProactiveEngine` runs observers that produce
`Observation`s — things that appear to be true and might matter. It holds no ability to act.
What an observation becomes is decided by one test, in one place
(`Observation.may_act_alone`):

    read-only  ·  reversible  ·  nothing leaves the machine  ·  below MEDIUM risk
        → may simply be done
    anything else
        → must be offered

Deliberately hard to pass. Reading the calendar passes. *Drafting the document the owner
will obviously need* does not — not because drafting is dangerous, but because a file they
did not ask for is a file they did not expect, and the entire difference between a helpful
assistant and an alarming one lives in that gap.

**One place, not nine.** The test is a property on the observation rather than a judgement
each observer makes, so "safe action" cannot come to mean different things in different
observers — which is how a system that was careful in nine places becomes uncontrolled in
the tenth.

**An offer is held, and answered once.** `OfferBook` keeps what Thursday suggested.
Offers expire (a question about tomorrow's meeting is dead the day after), and one "yes"
answers one question — an owner agreeing to a list has agreed to *something*, and nobody,
including them, could say which.

**An accepted offer is ordinary work.** It becomes a task and goes down the same path as
anything typed: planner, permission engine, agents, Supervisor, audit. A proactive request
that shortcut any of those would be a second execution path, and the one thing V10 must not
introduce is a way for Thursday to act on its own initiative *and* on its own terms.

**Saying it twice is nagging.** Observations carry a fingerprint and are deduplicated over
`REPEAT_WINDOW`; a worker loop noticing the same meeting every minute produces one message.

## Consequences

- `IntentKind.APPROVE` was parsed before V10 and handled nowhere: saying "ทำเลย" did
  nothing. It is handled now, along with a new `DECLINE`, and both are matched as *complete
  short utterances* — anchoring is what keeps "confirm the booking" (an instruction) and
  "ใช่ไหมครับ" (a question) out of a rule that means "yes, the thing you just asked".
- When an approval and an offer are both outstanding, **the approval wins**. Both are
  answered with the same word; the approval gates work already under way and was asked for,
  and guessing wrong on it either blocks work the owner released or releases work they meant
  to leave alone, while guessing wrong on an offer means a suggestion is re-asked later.
- **Cost we accepted:** Thursday will sometimes ask about something it could safely have
  done. That is the correct direction to be wrong in.
- One observer raising an exception cannot silence the others. A proactive layer that goes
  quiet because one check threw fails exactly when something is wrong.

## Alternatives considered

- **Let a model decide what is safe to do unprompted.** Rejected: the decision would be a
  generation, unreviewable, and different every time for the same facts.
- **A single "autonomy level" that gates everything.** Rejected — it already exists and does
  a different job. Autonomy says how much Thursday may do *when asked*; this says what it may
  do *unasked*, and collapsing them would mean raising one to raise the other.
- **Act freely on anything reversible.** Rejected: reversible is not the same as expected. A
  draft is trivially deletable and still arrives as a surprise.
