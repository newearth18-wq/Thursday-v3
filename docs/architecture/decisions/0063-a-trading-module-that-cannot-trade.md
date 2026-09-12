# 63. A trading module that cannot trade

Date: Sprint 94

## Status

Accepted. Fills Phase 11 of [the roadmap](../../13-roadmap.md) — the optional trading
module, which did not exist.

## Context

§15 of the brief asks for a trading module and attaches three conditions to it: do not
promise profitability, live execution must not bypass the Risk Manager, and a strategy
progresses backtest → paper → limited live → live rather than arriving at the last one.

Every other module in this project can be wrong and recovered from. A wrong lesson plan is
rewritten; a wrong video is re-rendered; a wrong memory is corrected. A wrong order is money
that has already left, and the person it left is the owner of a machine that was told to be
helpful.

The obvious reading of §15 is "build the module, and put a careful check in front of the
live path". That reading has a hole in it. A check in front of a live path is a piece of
code, and the question is not whether it is written correctly today — it is what happens
the day somebody adds a second call site, or a model composes a plan the check was not
written for, or a configuration flag is flipped while debugging at 2am. The check is the
thing that has to be right every time, forever, and the live path is right there behind it.

## Decision

**There is no live path.** Not a disabled one, not a guarded one, not one behind a flag.

`Stage.LIVE` resolves to `NoLiveBroker`, whose `execute` refuses with a reason naming
`ports.py`, and there is no other adapter in the package. `LIMITED` — the fourth rung, "live
with a small allocation" — runs on the **paper** broker and its report says so, because a
rung that quietly became real would be the worst of the four.

**And the verb is blocked anyway.** `trade.execute` is in the Permission Engine's
`HARD_BLOCKED` set: ADMIN, BLOCK, no grant, no override, no autonomy level that reaches it.

That is two refusals for one act, which is the point. They fail differently:

* The **absent adapter** is what a bug cannot get past. No amount of wrong reasoning
  upstream produces an order at a venue, because there is no code that talks to one.
* The **blocked verb** is what a future adapter cannot be switched on behind. The day
  somebody writes a broker client, they discover the verb is blocked before they discover
  it works.

Either alone would be defensible. Both is cheap, and the cost of being wrong here is not
symmetric with the cost of being careful.

**`Order` is constructed in exactly one place.** A `Signal` is what a strategy noticed and
carries no size; an `Order` is what could be sent. `RiskManager.size()` is the only
constructor call in the package, and `test_trading_risk_v14.py` walks the AST of every
module to assert it. "Live execution must not bypass the Risk Manager" is then a property of
the type graph rather than a rule somebody has to remember at each call site.

**Sizing is risk-first.** Quantity comes from what the account will lose if the stop is hit,
then the position cap trims it. The other direction — pick a position size, see what the
stop implies — makes the loss whatever the stop distance happened to be that day. A signal
with no stop is refused rather than given a default, because a position whose loss has no
floor is the one that ends an account.

**A backtest reports; it does not rate.** `StageResult.verdict` is the string `"ran"`, and
there is no `"passed"`. Whether twenty trades at a 1.2 profit factor is good enough to
progress is the owner's judgement, and a threshold invented here would be this project
inventing a standard for somebody else's money. `Progression.blockers()` lists what is
missing — the previous stage unrecorded, too few trades, no approval — and stops.

**Approval is a condition, not a bypass.** `approve()` is the only thing that sets it,
nothing in the module calls it, and approving `LIVE` with the rungs below it unrecorded
leaves `LIVE` blocked. Consent does not substitute for evidence.

**The disclaimer is in the payload.** Not in a docstring, not in the UI layer, not in the
prompt — in the dict `to_dict()` returns, so nothing downstream can quote the return figure
without carrying the sentence that says what it is.

## Consequences

The `trading` agent is READ with no tools and `local_only`. It can run a backtest, size a
hypothetical order so the owner can see the arithmetic, and say where a strategy sits on the
ladder. It has no `execute` action and no `stage` input — a stage parameter is the shape a
request for a live order would take, so `size` is always `PAPER` and a caller who supplies
one is ignored rather than obeyed.

**Nothing here forecasts.** The one strategy in the repository is an SMA crossover, present
so the engine has something to run. There is no model anywhere in `thursday_trading`.

**What this costs.** Somebody who genuinely wants Thursday to place orders cannot have that,
and unblocking it is not a setting — it is a pull request that adds an adapter and takes a
verb out of the block set, reviewed as such. That is the intended difficulty.

**What it does not cost.** Everything up to the order is real: the backtest walks bars in
order and fills on the bar *after* the signal, the paper broker's slippage always works
against the fill, the drawdown cap halts the run mid-way and the report says so. The
arithmetic is not a mock.

**Lot size defaults to zero**, meaning fractional quantities are allowed. Guessing a venue's
board lot would be a fiction; an account that names one (the SET trades in hundreds) gets
its quantity rounded **down**, never up, because rounding up puts more at risk than the
figure the sizing was asked for.
