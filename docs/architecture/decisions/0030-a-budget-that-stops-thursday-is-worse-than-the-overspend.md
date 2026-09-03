# ADR 0030 — A spending cap degrades; it does not stop

**Status:** accepted · **Date:** 2026-09-02

## Context

Per-task budgets (§61) were already enforced: `Task.budget`, `Spend.exceeds`, and a
`BudgetExceeded` raised the moment a task ran over. Auditing them for Sprint 45 turned up two
problems, and the second is the kind that makes a metric worse than no metric.

**A per-task budget bounds one task.** Fifty cents stops one runaway task and does nothing
about five hundred small ones. Nobody sets out to spend a hundred dollars; they spend it
forty cents at a time over a fortnight, and every individual charge passed its check.

**Spend was counted where an agent chose to count it.** `AgentContext.think` added to its own
ledger — which missed the two model calls every turn makes: the reasoning pass that
interprets the utterance and the supervisor pass that verifies the result. Neither is an
agent. A system could hold a conversation all day and report zero, and the zero would look
like good news.

## Decision

**Meter at the router.** `ModelRouter.complete` records every completion — provider, tier,
tokens, cost — because it is the single point every model call passes through. Reporting your
own spend is optional in practice, and the two call sites that turned out not to be reporting
were the two that run on every turn. This is the same argument that puts authorization in one
engine rather than in each caller.

**A ceiling above any single task.** `CostMeter` holds a daily and a monthly cap over a
ledger of charges, checked *before* a paid call. After is an accounting record of money
already gone; a per-task budget can be checked after a charge because it bounds a task that
is already running, but a spending ceiling has to bound the next call or it bounds nothing.

**Reaching the cap routes to the local model, which is free.** It does not refuse the work:

> a ceiling that stops Thursday working is worse than the overspend it prevents

An owner cannot tell that kind of outage from a broken assistant, so they fix it by deleting
the cap — which is the opposite of what the cap is for. Refusing is the last resort, for a
deployment with no local model, and then it says so as a budget problem rather than failing as
a model error: "the daily cap is reached" and "the provider is down" want different responses.

**The local model is never capped.** It costs nothing, and it is the floor the cap falls back
to. Throttling it would remove the thing that keeps a reached cap from being an outage.

**A cap is the owner's.** There is no method on `CostMeter` that widens a limit, and no
endpoint that raises one. An agent that finds the ceiling inconvenient, or a model asked
whether it should continue, has nothing to call. This is §95's shape applied to money.

**The owner is warned before it binds, once per period.** A warning that arrives with the
refusal is not a warning; one repeated every turn is one nobody reads. It goes in the brief's
`issues` rather than its `suggestions` — an approaching cap is something about to constrain
Thursday, not something being offered.

**A parked provider is tried again.** The circuit breaker cleared its counter only on a
success, and a parked provider was never selected, so it never succeeded, so it never
cleared: three transient failures disabled a good provider until somebody restarted the
process. After a cooldown it is offered one call, and that call decides. ADR 0028 had already
made this argument about recovery attempt windows; it simply had not been applied here.

## Consequences

- Cost figures mean something: the ledger total is exactly the calls that happened, and a
  zero in it is a real zero rather than an unreported one.
- `GET /api/v1/costs` and `/costs/detail` make the ceiling something the owner can watch
  approaching rather than discover when it binds.
- Under a reached cap, a turn whose success criteria need a reasoning model reports
  **unverified** rather than passing. §76 has no budget exemption, and a cap that quietly
  lowered the bar for "done" would be far worse than one that says what it could not check.
  This is the same behaviour offline mode already has.
- **Cost we accepted:** the ledger lives in memory and is lost on restart, so a cap can be
  reset by a restart. Recording it durably is the obvious next step and is named as a gap
  rather than implied to be done.

## Alternatives considered

- **Refuse at the cap.** Rejected: it converts a budget into an outage, and the owner's
  remedy is to remove the budget.
- **Meter in the agent context, and add the two missing call sites.** Rejected — that fixes
  today's two and leaves the next call site to be forgotten. The router cannot be forgotten.
- **Estimate cost from token counts at the call site.** Rejected: providers already report
  their own usage, and an estimate that drifts from the bill is a number nobody trusts.
- **Let a high-priority task exceed the cap.** Rejected: every task believes it is important,
  and the judgement of importance is made by the thing being limited.
