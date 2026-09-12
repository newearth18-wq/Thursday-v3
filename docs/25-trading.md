# 25. Trading (V14)

    bars → strategy → signal → risk manager → order → paper broker → report

Optional, isolated, and unable to place an order. That last part is not a limitation waiting
to be lifted; it is the design
([ADR 0063](architecture/decisions/0063-a-trading-module-that-cannot-trade.md)).

§15 of the brief attaches three conditions to this module: do not promise profitability,
live execution must not bypass the Risk Manager, and a strategy progresses
backtest → paper → limited → live. Everything below follows from taking the second one
structurally rather than procedurally.

## Two refusals for one act

| | What it stops |
|---|---|
| **No live adapter** | `Stage.LIVE` resolves to `NoLiveBroker`, which refuses and names `ports.py`. There is no other adapter in the package — nothing upstream can be wrong in a way that reaches a venue |
| **A blocked verb** | `trade.execute` is in `HARD_BLOCKED`: ADMIN, BLOCK, no grant, no override, no autonomy level. The day somebody writes a broker client, they find the verb blocked before they find it working |

They fail differently on purpose. The missing adapter is what a bug cannot get past; the
blocked verb is what a future adapter cannot be switched on behind.

`LIMITED` — the rung the brief describes as live with a small allocation — runs on the
**paper** broker, and the report says so. A rung that quietly became real would be the worst
of the four.

## `Order` is built in one place

A `Signal` is what a strategy noticed. It carries a side, a price, a required stop, and no
size. An `Order` is what could be sent, and `RiskManager.size()` is the only call that
constructs one anywhere in the package —
`tests/unit/test_trading_risk_v14.py` parses every module and asserts it.

So "live execution must not bypass the Risk Manager" is a property of the type graph. A
strategy that could construct an `Order` would be a strategy that could size its own
position, and the rule would be a convention somebody has to remember at each new call site.

## Sizing is risk-first

```
risk budget = equity × risk_per_trade      1% of 100,000 = 1,000
quantity    = risk budget ÷ (entry − stop)  1,000 ÷ 5     = 200 units
then        position cap trims it           notional ≤ 20% of equity
then        lot size rounds it down         never up
```

Derived the other way — pick a position size, see what the stop implies — the loss becomes
whatever the stop distance happened to be that day. A signal with no stop is refused rather
than given a default: a position whose loss has no floor is the one that ends an account.

Every refusal names the number that caused it. `lot_size` defaults to `0`, meaning fractional
sizes are allowed, because guessing a venue's board lot would be a fiction; an account that
names one (the SET trades in hundreds) gets the remainder dropped, since rounding up would
put more at risk than the sizing was asked for.

| Limit | Default | What it is |
|---|---|---|
| `risk_per_trade` | 1% | Of equity, measured to the stop — not the position size |
| `max_position` | 20% | One position's notional |
| `max_daily_loss` | 3% | Realised, against the day's starting equity; then the day stops |
| `max_drawdown` | 10% | From the high-water mark; then everything stops |
| `max_positions` | 5 | Open at once |
| `lot_size` | 0 | Smallest tradeable increment; 0 means fractional |

Defaults are conservative rather than typical, because a default that is merely typical
becomes the setting nobody revisited. `Account.peak` only ever rises, so a restart cannot
reset a drawdown cap by accident.

`halt()` stops everything and nothing in the module calls `resume()` — coming back is the
owner's decision, made deliberately.

## The backtest walks forwards

One pass, in order, and the order of operations inside a bar is the whole correctness
argument:

1. An open position is checked against its **stop** before anything else happens
2. A signal from the **previous** bar fills on this one — never the bar that made it
3. Mark to market, then let the caps look at the result
4. Ask the strategy, having shown it `bars[:i+1]` and nothing after

A signal is good for exactly one bar. An earlier version kept a blocked signal pending and
could fill a seven-bar-old idea thirty bars later, at a price the strategy never saw. The
paper broker's slippage always works against the fill, and fills come at the bar's **open**,
not a close the signal could not have reached.

An open position at the end is closed at the last close and labelled
`ปิดท้ายช่วงทดสอบ` — a report that leaves it open counts an unrealised gain as if it were
real. A manager that was already halted sets `report.halted` before the loop, so a stopped
account produces a report that says it is stopped rather than an empty one that reads as
"the strategy found nothing".

## It reports; it does not rate

`StageResult.verdict` is the string `"ran"`. There is no `"passed"`, and there is no
threshold in this repository that says twenty trades at some profit factor earns the next
rung. That is the owner's judgement about the owner's money, and a number invented here
would be this project inventing a standard nobody adopted.

`Progression.blockers()` lists what is missing and stops: the previous stage unrecorded,
fewer than `MIN_TRADES` trades, no approval. `approve()` is the only thing that sets
approval, nothing in the module calls it, and approving `LIVE` with the rungs below it
unrecorded leaves `LIVE` blocked — consent is one of the conditions, not a way around the
others.

Every report carries its disclaimer **in the payload** rather than in a docstring or a UI
layer, so nothing downstream can quote the return figure without the sentence that says what
it is: *ผลลัพธ์นี้คือสิ่งที่กฎชุดนี้ทำกับข้อมูลชุดนี้ ไม่ใช่การคาดการณ์อนาคต และไม่ใช่คำแนะนำการลงทุน.*

## The agent

READ, no tools, `local_only`, three actions — `backtest`, `size`, `ladder`. No `execute`,
and no `stage` input: a stage parameter is the shape a request for a live order would take,
so `size` is always `PAPER` and a caller who supplies one is ignored rather than obeyed. Its
`user_description` says ส่งคำสั่งซื้อขายจริงไม่ได้ before the owner asks.

The report's `count` and `items` are surfaced on the result so the Supervisor's arithmetic
check recomputes the trade count rather than taking the summary's word for it, and a `size`
report carries the limits that produced it — a quantity with no rules beside it is a figure
the owner has to take on trust.

## What is deliberately not built

**Market data.** No feed, no broker API, no scraper. Bars arrive as inputs.

**Prediction.** There is no model anywhere in `thursday_trading`. The one strategy in the
repository is a moving-average crossover, present so the engine has something to run, and it
sees only the bars handed to it.

**Portfolio optimisation, options, leverage, shorting beyond a `SELL` side.** Each is a
larger module whose failure mode is a number nobody checks.

## Where it lives

```
packages/trading/thursday_trading/
  models.py    Side, Stage and its explicit rank, Bar, Signal, Order, Fill
  risk.py      Limits, Account, RiskManager — the only place an Order is built
  stages.py    StageResult, Progression, the blockers, the owner's approval
  ports.py     Broker protocol, PaperBroker, NoLiveBroker, broker_for(stage)
  backtest.py  run() — one forward pass, and the report it produces
```

Tests: `tests/unit/test_trading_{risk,stages,backtest}_v14.py`,
`tests/integration/test_trading_agent_v14.py` (the agent, the block set, and the absent
adapter).
