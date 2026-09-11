# 52. A tutor is a view of the system, not a description of it

Date: Sprints 67–72 (ADAPTIVE ONBOARDING & PERSONAL TUTOR)

## Status

Accepted. Extends [0012](0012-verification-before-completion.md) to lessons and
[0051](0051-a-repair-button-is-not-a-way-around-the-permission-engine.md)'s reasoning to a
second surface that looked like a way around the Permission Engine.

## Context

The onboarding requirement asks Thursday to teach its own use: a first-run introduction,
lessons, contextual tips, a Learning Center, and an agent to run them. Its own §52 states the
constraint that shapes all of it — *"Tutor obtains capabilities from same registry as Thursday
Core. Therefore tutorial always reflects real installation."*

That sentence is doing more work than it looks. A tutor is, structurally, a **second
description of the system**, and second descriptions drift. The drift is not hypothetical and
it is not slow: a lesson written when the camera worked stays written after it is unplugged,
and §12 names exactly that failure — a beginner told to "ลองเปิดกล้องเลย" on a machine with no
camera. The tutor is also the one component whose whole job is to be believed, by the person
least equipped to notice it is wrong.

Two further parts of the requirement are shaped like holes in the security model. §23's
Practice Mode wants to demonstrate deleting a file and sending an email without doing either.
§46–§48 want an agent with enough reach to teach and none to act.

## Decision

**Availability is derived at read time, never stored.** A `Feature` carries a `probe` — a
function of the live container — instead of an `enabled` flag. A test asserts no such field
exists on the type, because the failure mode of a flag is silence: it is right the day it is
written and wrong the first day the machine changes. §12's alternative travels with the
refusal, so "เครื่องนี้ยังไม่พบกล้อง" arrives with "คุณใช้กล้องจากมือถือ…" attached; the first
is where somebody stops and the second is where they carry on.

**A lesson ends when the machine proves it.** [0012](0012-verification-before-completion.md),
applied one layer up. `Step.verify` reads what an attempt left behind — the node observed the
app open, the store actually holds the memory — and `LessonRunner.attempt` takes evidence with
no parameter through which a caller could assert success. The same shape as `/setup/verify`
(Sprint 64) and the updater's missing URL parameter (ADR 0033), for the same reason: a
completion flag a caller can set is one a caller will set. The default `verify` returns False,
so a step whose author forgot to say how to check it can never pass.

**Practice Mode is a description, not a flag.** The two implementations are almost
indistinguishable from outside — `if practice: don't really do it`, versus something that never
executes — and the first is a bypass with a reassuring name. It runs the real path holding a
boolean, one missed branch from sending the email, and it asks the Permission Engine about an
action nobody intends to take. So `practice.py` has no execution path: tests walk its imports
(no engine, no hub, no tool registry), walk its AST (no `ActionRequest`, no dispatch call),
and assert that rehearsing a delete leaves no approval behind — because approval state is real
even when the send is not.

**The Tutor agent's limits are structural.** §48's list is not checked inside the tutor; that
would be a second permission system to keep in agreement with the first, and its disagreements
would surface as an agent doing something nobody sanctioned. Instead: `tools=[]`, a READ
ceiling below every action on the list, capabilities confined to `tutorial.*` — a namespace
with no executor behind it — and LOCAL_ONLY, because a lesson is about the owner's own machine.

**Teaching is gated before it is scored.** §50 wants a score and §51 a cooldown, and written
naively they argue. The order is: teaching frequency (a ceiling — OFF means off), then the
existing `ProactivityGate` shared with notifications, then a cooldown, and only then a score
that chooses *which* tip. `may_speak()` takes no capability argument, so it cannot be swayed by
how good a particular tip would be. A score that could decide *whether* to interrupt is one
somebody eventually tunes upward.

**Being told is not knowing.** The capability profile advances to DISCOVERED on an
introduction and no further; LEARNED needs more than one success; MASTERED needs repeated use
*and* a week with no help offered, because MASTERED is the state that removes explanation.
Familiarity decays after ninety days unused. A profile that only climbs eventually claims the
owner is expert at everything.

## Consequences

Three defects surfaced by running this rather than reading it, all of the family this project
keeps finding — a claim that was documented and untrue.

Tips were metered through the gate at `LOW`, which reads well: a tip *should* be the first
casualty of a busy hour. But `ProactivityGate` only admits `LOW` at proactivity HIGH, and the
shipped default is NORMAL, so `teaching: normal` promised occasional tips and delivered none on
every default install. Two settings each defensible alone and wrong together (ADR 0049 again).

The upgrade tip fired after a single use with a score of 0.79, while its own text read
"คุณให้ผมหาไฟล์บ่อย" — a message asserting a frequency that had not happened. The threshold is
a veto now rather than a weight, because a tip whose text is false is worse than no tip.

And the §65 acceptance test, walking the journey a beginner walks, found that learning to stop
Thursday disconnects every node — correct behaviour (§69), and it means the next lesson
genuinely cannot run until something reconnects. The Learning Center reports that rather than
hiding it, which is the tutor staying truthful through a consequence of its own lesson.

§61's extension point is real: `AgentSpec` and `ToolSpec` carry `user_description`,
`user_examples`, `safety_notes` and `requirements`, and an agent that supplies one is taught
without editing the catalogue. An agent that does not is **skipped** rather than described by
its developer-facing `description` — Sprint 65's allowlist rule, applied to features. Five of
fourteen agents currently describe themselves, and the other nine are silent, which is the
correct and visible default.

The Learning Center is API-only. There is no UI for any of it, and the desktop app does not
render it yet.
