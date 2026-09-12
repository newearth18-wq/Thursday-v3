# 26. Workflows (V15)

    [trigger] ──▶ [condition] ──▶ [condition] ──▶ [action] ──▶ [action]
                   all must hold                  in order, then follow-ups

The brief's last unbuilt item: a node-based builder for the automation engine. The engine
has existed since Phase 4 and had no screen, no API, and — for one of its four trigger
kinds — no runner.

## The runner came first

`Trigger(kind="schedule")` had been in the type since the engine was written, and **nothing
had ever fired one.** `should_fire` answers only for event triggers; no loop anywhere looked
at a clock. `routines.py` generated suggested routines with cron triggers that could not
run. A rule saying *every weekday at 07:30* was stored, listed, and did nothing.

Shipping a canvas on top of that would have multiplied the silence by however many rules the
owner felt confident enough to draw
([ADR 0064](architecture/decisions/0064-a-canvas-may-not-draw-what-the-engine-cannot-run.md)).
So `cron.py` came first:

| | |
|---|---|
| Fields | `minute hour day month weekday`, POSIX order, inclusive ranges |
| Accepts | `*`, `N`, `a-b`, `*/n`, `a-b/n`, lists, `MON`/`JAN` names, `7` for Sunday |
| Evaluated in | `settings.timezone` — 07:30 means 07:30 where the owner is |
| Refuses | anything it cannot parse, rather than narrowing it to `*` |

**The rule implementations differ on.** With *both* day-of-month and day-of-week restricted,
cron fires when **either** matches. `0 0 1 * MON` is "the first of the month, and also every
Monday" — not "Mondays that fall on the first". Read as "and", that rule fires roughly once
a year, and the owner finds out by it not having happened. It is implemented, named in the
code, and tested both ways round.

No dependency: a cron field is a small grammar, and the part that matters is the part a
library would let you assume.

The worker sweeps once every 30 seconds. The engine records which minute each rule was
served, so a loop that is not minute-aligned — and it is not — fires once per minute rather
than twice.

## The canvas draws what the engine runs, and nothing else

One trigger, an AND-set of conditions, an ordered list of actions. No OR node, no branch, no
loop, no sub-workflow, because the engine has none of those and drawing one would be drawing
a promise.

**The wires are derived.** Given that shape there is nothing to connect and no way to
connect it wrongly, so the owner is not offered arrows — offering them would offer an arrow
the engine ignores. What they drag is position, which is theirs and means nothing to the
rule. Every drag is clamped to the canvas: a node dragged out of sight is a node the owner
has lost.

**The run order is buttons, not geometry.** Actions run in order, so the order is behaviour.
A rule that changed because a box was dropped an inch to the left would be a rule edited by
accident.

## What the owner is told before anything runs

`POST /automations/preview` writes nothing, runs nothing, and is called on every edit. Per
action it returns:

| | |
|---|---|
| **action** | the permission verb this resolves to |
| **level** / **decision** | what the Permission Engine would say — `READ`/`AUTO` through `ADMIN`/`BLOCK` |
| **blocked** | in the permanent block set, shown on the canvas as **ห้ามถาวร** |
| **unavailable** | the handler is not wired, so this step would be skipped in silence |

That last one is the failure a picture hides best. The engine returns `{"skipped": kind}`
when the executor or task manager is absent, which from outside is indistinguishable from a
rule that ran and found nothing to do. Saying so while the owner is looking at the canvas is
the only moment it is cheap to say.

The rule also reads back as one sentence — *07:30 วันจันทร์ถึงศุกร์ แล้ว แจ้งเตือน* — and a
cron expression is described in words as it is typed, because "is this what I meant" is a
question best answered before the rule is saved rather than after it fails to run. An
expression that cannot be read gets the refusal, never a confident sentence.

## Saving is not arming

`POST /automations` stores the rule with `enabled=False` **whatever the body says**. Enabling
is a separate call, made after reading the preview. An edit to an existing rule keeps
whatever the owner last decided about whether it runs: it neither arms a parked rule nor
disarms a live one.

`POST /automations/{id}/run` works on a disabled rule on purpose — trying it before arming
it is the point of the button. Every action inside still passes the Permission Engine;
running by hand grants the rule nothing it would not have had.

## A trigger with no runner says so

`state_change` is in the `Trigger` type and nothing watches world state. The button is
disabled and carries the reason, rather than being left off the menu — which answers "why
can't I do X" with silence, and leaves nothing to turn green the day somebody writes a
watcher.

The whole catalogue is **served, not hardcoded**: which triggers run, which handlers are
wired, and every tool's permission level and decision, all read off the container that is
actually running. A menu compiled into the front end drifts from the truth and nothing
notices.

## Both sides refuse rather than correct

A graph with an error does not save. Nothing drops a dangling node, invents a missing field,
or narrows an unparseable cron. `lib/workflow.ts` validates locally so typing feels
responsive, and it is never the verdict — the server validates the same graph and its answer
is the one that counts.

Two refusals worth naming, because each is a rule that would otherwise never fire and never
say why:

*A numeric comparison against a word.* `gt` on a non-number compares NaN, which is false for
everything. The condition never holds, the rule never runs, and nothing errors.

*A second trigger.* The engine has one `trigger` field, so the extra box would be silently
dropped and the picture would not say which one survived.

## What is deliberately not built

No branching, no loops, no sub-workflows, no variables passed between actions, no undo
stack. Each is a real feature and each needs engine support that does not exist. Adding the
picture first is exactly how a builder starts lying.

## Where it lives

```
packages/automation/thursday_automation/
  cron.py       five-field parsing, matching, plain-language reading
  workflow.py   the node graph, validation, conversion both ways, the preview
  engine.py     due() and sweep() — the schedule runner that did not exist
services/api/thursday_api/routers/automations.py   eight routes under a prefix that had none
services/worker/thursday_worker/jobs.py            sweep_schedules, once every 30s
apps/desktop/src/lib/workflow.ts                   the canvas as arithmetic
apps/desktop/src/components/WorkflowBuilder.tsx    the canvas as pixels
```

Tests: `tests/unit/test_{cron,workflow,schedule_sweep}_v15.py`,
`tests/integration/test_workflow_api_v15.py`, and
`apps/desktop/src/{lib/workflow,components/WorkflowBuilder}.test.tsx`.
