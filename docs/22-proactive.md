# 22. Proactive Thursday (V10)

    observe → analyse → offer → (the owner answers) → the ordinary path

## Noticing is not doing

This is the layer where Thursday speaks first, and the goal statement contains its own
constraint: *ห้ามกลายเป็น autonomous system ที่ทำอะไรก็ได้เอง*. The whole design rests on one
distinction ([ADR 0027](architecture/decisions/0027-noticing-is-not-doing.md)):

| | |
|---|---|
| read-only · reversible · local · below MEDIUM risk | **may simply be done** |
| anything else | **must be offered** |

Deliberately hard to pass. Reading the calendar passes. *Drafting the document the owner will
obviously need* does not — not because drafting is dangerous, but because a file they did not
ask for is a file they did not expect, and the entire difference between a helpful assistant
and an alarming one lives in that gap.

The test is a property on the `Observation`, decided in **one place**, so "safe action"
cannot come to mean different things in different observers.

## The event kinds

`calendar.upcoming` · `email.received` · `task.deadline` · `task.completed` ·
`device.offline` · `file.changed` · `project.blocked` · `automation.triggered` ·
`system.warning`

Observers registered against `ProactiveEngine` return observations; the engine applies the
`ProactivityGate` (level, owner status, people present, rate limit) and its own
deduplication. One observer raising cannot silence the others — a proactive layer that goes
quiet because a check threw fails exactly when something is wrong.

**Volume is a safety property.** An assistant right nine times in ten that speaks forty times
a day gets turned off, and one that has been turned off has a safety record of zero. The same
fact is raised once per `REPEAT_WINDOW`.

## Offers, and answering them

```
(Thursday) "พรุ่งนี้มีประชุมและยังไม่พบเอกสารเตรียมประชุม ต้องการให้ผมจัดเตรียมให้ไหมครับ"
(owner)    "ทำเลย"
           → task → research + document agents → Supervisor → verified result
```

`"ทำเลย"` parsed as an approval before V10 and **nothing handled it** — saying it did
nothing at all. Now `APPROVE` and a new `DECLINE` are both handled, matched as *complete
short utterances*: anchoring at both ends is what keeps "confirm the booking" (an
instruction) and "ใช่ไหมครับ" (a question) out of a rule that means "yes, the thing you just
asked me".

| Rule | Why |
|---|---|
| Offers expire | A question about tomorrow's meeting is dead the day after |
| One yes answers one question | Agreeing to a list is agreeing to *something*, and nobody could say which |
| **An approval outranks an offer** | Both use the same word; the approval was asked for and interrupted them |
| An accepted offer is ordinary work | Same planner, permissions, agents, Supervisor, audit |

That last row is the load-bearing one: a proactive request that shortcut any of those would
be a second execution path, and V10 must not add a way for Thursday to act on its own
initiative *and* on its own terms.

## Goals above tasks

    GOAL → MISSION → PROJECT → TASK → ACTION

A system that knows only about tasks can say what it did, not whether any of it mattered.
Goal progress is *missions done over missions total* — never weighted by task counts, which
would measure effort rather than progress. A task inherits its goal's priority, so "why is
this ahead of that" has an answer above the level of whoever typed a number.

**Preemption preserves state.** Important work pauses lower work rather than cancelling it —
`PAUSED` is defined as resuming where it stopped — and a task the state machine will not
pause is left running rather than forced. Without that, "higher priority" quietly means
"destroys lower-priority work".

`Task.priority` was a bare `int` on an undocumented 1–10 scale; it is `Priority` now, which
also fixes a silent bug: assigning the enum to an `int` field made Pydantic coerce it away,
and every preemption check then failed.

## Briefings and the decision journal

Morning: calendar, deadlines, waiting-on-you, system issues, suggestions. End of day: what
finished, what is blocked, what failed, decisions taken, skills learned. Both assembled from
state that already exists — **no model** — because a summary of the day that invents one item
is a summary nobody can use.

The decision journal records the **alternatives**. Over months a system with memory, learned
skills and standing automations accumulates choices nobody remembers making; the options not
taken are what turn a log line into something a person can re-decide.

## Learning from being corrected

[ADR 0028](architecture/decisions/0028-one-correction-is-not-a-rule.md). A single "แบบนี้ไม่
เอา" might mean *never*, or *not this document*, or *not today*, or *you misunderstood*.
Storing the strongest reading of an ambiguous signal is how an assistant progressively stops
doing things for reasons nobody remembers.

So a correction is a `FeedbackEvent`. Only a subject corrected `CONFIDENCE_REPEATS` times,
recently, becomes a proposal — and even then it is a question, because an agent may not write
the owner's preferences (PART 76). To say something once and have it stick, say it as an
instruction: that already works, and it is `MEMORY_WRITE` (ADR 0018).

Self-evaluation reads the record — verified, retries, whether the owner rewrote the result —
and never asks a model how it did. Agent scores are a record, not a ranking: an agent handling
hard jobs scores below one handling easy jobs.

## Self-recovery

| May be repaired automatically | May never be |
|---|---|
| restart a worker · retry a safe request · switch model · switch agent · reconnect a node · clear a cache | change security · change a permission · disable protection · disable audit · install a component · admin repair · rotate a credential · grant access |

The line is not danger, it is direction: every allowed repair **restores a capability the
system already had**; every forbidden one **changes what it is permitted to do**. A system
that can widen its own permissions to fix itself has no permission model, only a delay.

A forbidden repair is refused *at wiring time*, not at call time — one that exists and merely
is never invoked is one line away from being invoked. Attempts are bounded per component and
exhausting them escalates into the brief, because a recovery that loops for ever is an outage
that hides itself from the one person who could fix it.

## Not built yet

The **worker loop that calls `sweep()` on a schedule**. Everything it would call is built and
tested — observers, gate, deduplication, offers — and the periodic trigger that would make
Thursday speak without being spoken to first is not wired into the background worker. Today a
sweep happens when something asks for one.

`email.received` and `file.changed` have no source. There is no mail adapter (V9's messaging
port is drafts-only and local) and no filesystem watcher, so those two event kinds are named
and unproduced.

GPU usage is in the spec's budget list and is not modelled; `Budget` covers tokens, cost,
time, tool calls and agent calls.
