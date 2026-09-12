# 64. A canvas may not draw what the engine cannot run

Date: Sprint 95

## Status

Accepted. Fills the last unbuilt item of the brief: the node-based workflow builder. The
automation engine has existed since Phase 4; the screen for it did not.

## Context

The brief asks for a visual workflow builder — drag nodes, connect them, build an
automation. Every such tool looks the same in a screenshot, and they differ entirely in
whether the picture is true.

Two things were already wrong before a single pixel was drawn, and both were invisible:

**`Trigger(kind="schedule")` never fired.** It has been in the type since the engine was
written. `Automation.should_fire` answers only for event triggers, and no loop anywhere
looked at a clock. `routines.py` generated suggested routines with cron triggers that could
not run. A rule saying *every weekday at 07:30* was stored, listed, and did nothing —
silently, forever.

**An action whose handler is absent returns `{"skipped": kind}`.** From outside that is
indistinguishable from a rule that ran and found nothing to do. On a fresh install, a rule
whose only action is a tool call reports success and does nothing.

A builder shipped on top of that would have multiplied both by the number of rules an owner
felt confident enough to write. The screen is where a wrong promise is made most
convincingly.

## Decision

**Build the runner before the canvas.** `thursday_automation/cron.py` parses five-field
expressions and the worker sweeps them once a minute. Written out rather than imported,
because the behaviour that matters is the one implementations differ on: with **both**
day-of-month and day-of-week restricted, cron fires when **either** matches. Read as "and",
`0 0 1 * MON` fires about once a year and the owner finds out by it not having happened.

**The canvas draws the engine's shape, not a dataflow language.** One trigger, an AND-set
of conditions, an ordered list of actions:

    [trigger] ──▶ [condition] ──▶ [condition] ──▶ [action] ──▶ [action]

There is no OR node, no branch, no loop and no sub-workflow, because the engine has none.
Drawing one would be drawing a promise.

**The wires are derived, never dragged.** Given that shape, there is nothing for the owner
to connect and nothing they could connect wrongly. Offering arrows would offer an arrow the
engine ignores, and a picture that disagrees with behaviour is worse than no picture. What
the owner drags is position, which is theirs and means nothing to the rule.

**The run order is edited with buttons, not by dropping.** Actions run in order, so the
order is behaviour. A rule that changed because a box was dropped an inch to the left would
be a rule edited by accident.

**Every edit asks the server what the rule would actually do.** `/automations/preview`
writes nothing, runs nothing, and returns three things per action: the permission verb it
resolves to, the Permission Engine's decision on it, and whether the handler is wired up at
all. A `BLOCK` shows on the canvas as **ห้ามถาวร** while the owner is still drawing, rather
than at 3am as a rule that never ran.

**Saving is not arming.** `POST` stores the rule with `enabled=False` whatever the request
body says; enabling is a separate call the owner makes after reading the preview. An edit
to an existing rule preserves whatever the owner last decided about whether it runs — it
neither arms a parked rule nor disarms a live one.

**A trigger with no runner is offered as unavailable, with the reason.** `state_change` is
in the type and nothing watches world state, so the button is disabled and says why. The
alternative — leaving it off the menu — answers "why can't I do X" with silence, and the
day somebody writes a watcher, one line turns green.

**The catalogue is served, not hardcoded in the client.** Which triggers run, which handlers
are wired, what every tool's permission level and decision are: all from the container that
is actually running. A menu compiled into the front end drifts from the truth and nothing
notices.

## Consequences

`/automations` had been in the API's `EXPENSIVE_PREFIXES` list since rate limiting was
written, with no routes under it. It now has eight, and every rule — including ones
`routines.py` suggested — opens on the canvas. Otherwise the owner has two places rules
live: one they can see and one they cannot.

Local validation in `lib/workflow.ts` exists to make typing feel responsive and is never the
verdict. The server validates the same graph and its answer is the one that counts; anything
only it knows (a cron it cannot parse, a tool that does not exist) arrives from there.

**Both sides refuse rather than correct.** A graph with an error does not save. Nothing
drops a dangling node, invents a missing field, or narrows an unparseable cron to `*` — that
last one being the specific failure worth naming, since a rule the owner believes runs at
07:30 and which actually runs every minute is worse than one that would not save.

**What is deliberately not built.** No branching, no loops, no sub-workflows, no variables
passed between actions, no undo stack. Each is a real feature and each would need engine
support that does not exist; adding the picture first is how a builder starts lying.

The canvas is fixed-size rather than infinite: a node dragged out of sight is a node the
owner has lost, and every drag is clamped.
