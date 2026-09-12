# 68. An arrow is a claim about where something is

Date: Sprint 99

## Status

Accepted. Closes the §13 interactive walkthrough §23 has carried as open since the Learning
Center was built without a UI. The §22 gesture tutorial with a live hand skeleton remains
open, and §23 says so.

## Context

§13 asks for a walkthrough that highlights a real button with an arrow. It is the smallest
feature in this repository with the largest failure mode, and the failure is not visual.

An arrow is a **claim about where something is**. Point it at the wrong place once and the
owner stops trusting the next one — and unlike a wrong number in a document, they find out
instantly, while being taught. A walkthrough that is right most of the time is worse than
none, because the whole value is that the owner can follow it without checking.

There are exactly two ways to make that claim wrongly:

* the control is **not on screen** — a closed drawer, a button that only shows while
  something is running, a window resized since the arrow was drawn;
* the control **no longer exists under that name** — renamed in the interface three sprints
  after the lesson naming it was written. That is the drift ADR 0065 was written for, in a
  place where nothing would have noticed.

## Decision

**A lesson names a control; it does not describe where the control is.** `Step.points_at`
carries a stable name, the desktop marks its controls `data-teach="<name>"`, and the
interface finds the real one at the moment it draws. Nothing anywhere stores a coordinate.

**A name a lesson uses must exist in the app, enforced by test.**
`test_walkthrough_v19.py` scans `apps/desktop/src` for every `data-teach` and asserts every
`points_at` is among them. Renaming a control and leaving the lesson behind fails the suite,
which is the only moment it is cheap to find out.

**A control that is not on screen gets no arrow and a sentence saying so.** `locate` returns
null — including for a zero-sized box, because `display:none` and a detached node both
measure zero and an arrow at a zero-sized rectangle points at a corner of the screen — and
the caller must handle it. There is no "draw it roughly there" path.

**Empty is a real answer.** A step about what to *say* has nothing to point at, and
`points_at` is `""` rather than absent: one shape for the interface to handle rather than
two, and no temptation to guess a plausible target.

**It points; it does not press.** Nothing clicks the control, focuses it, or fills it in. The
owner doing the thing is the lesson — a walkthrough that performed the step would be the
"mark as complete" button the Learning Center refused (ADR 0065), wearing a different shape.

**The placement is arithmetic, in `lib/spotlight.ts`, and tested.** Which side the label goes
on, whether there is room, how far it is pulled back from an edge: geometry that is only ever
looked at is geometry that is never checked, and it is wrong in ways a screenshot does not
show. Above is preferred because the controls worth teaching in this interface sit near the
bottom. When no side has room the label is clamped on screen rather than dropped — half a
label is readable, and no label is a silent failure.

**It is re-measured while it is shown.** A drawer opens, the window resizes, the control
moves. A ring left where the button used to be is the same lie as an arrow pointing at
nothing.

## Consequences

Two controls carry `data-teach` today — the conversation input and "stop all" — because those
are the two lessons whose text is about a place rather than a phrase. The rest name nothing,
which is the honest answer and not an omission to be filled in later.

`how-to-stop` is the lesson this matters most for: it says *there is a button*, and a sentence
about a button the owner cannot find is worse than no sentence.

**What is not built.** No sequenced tour that advances by itself, no modal that blocks the
interface until the owner clicks the right thing, no dimming overlay. Each would take control
away from the owner during the one activity that exists to give it to them, and the first two
would also make the walkthrough responsible for the owner's next click — which is the
performing-the-step problem again.

**Still open:** §22's gesture tutorial with a live hand skeleton. It needs a camera, and no
camera has ever run against this code (§23).
