# 0076. A machine is bounded by what we sent it, not by what it last said

Date: 2026-09-13

## Status

Accepted. Implements ADDENDUM §129. Extends [0046](0046-a-privacy-rule-expressed-as-a-score-is-a-preference.md)
and [0047](0047-a-stage-cannot-be-less-private-than-its-task.md)
and the distributed runner of Sprint 59.

## Context

[§23](../../23-release-readiness.md) listed a gap:

> **Distributed stages run sequentially.** §21's example is naturally concurrent — vision on
> one machine while embeddings run on another — and the `needs` graph carries the information
> needed to parallelise. It waits on a per-device concurrency limit (§129), which does not
> exist; running stages in parallel without one would let a task saturate the machine it is
> running on.

That was true, and it was the smaller of the two things in the way.

### What the router knew about "busy"

Measured against the shipped router, with two equally capable machines both reporting
themselves idle:

```
THREE CONCURRENT CHOOSE() CALLS LANDED ON: ['machine-A', 'machine-A', 'machine-A']
DISTINCT MACHINES USED: 1 of 2 available
ROUTER ATTRS TRACKING DISPATCH: none
```

The router does consider load — `idle = -gpu_percent`, and `queue_depth` excludes a machine
outright for heavy work. But every one of those numbers comes from `ComputeLoad`, which a
node sends **with its heartbeat**. It describes the machine as of the last report. Three
stages dispatched in the same millisecond are all routed against the same pre-dispatch
snapshot, so all three prefer whichever machine looked idle, and the signal that would have
separated them arrives after the decision it was needed for.

A limit built on that number would not bind. It is not too coarse; it is too late.

### What a stage could read

The second finding was not in the release notes, and it is the one that had to be fixed
first:

```
stage 'alpha' declared needs=() and was handed []
stage 'beta'  declared needs=() and was handed ['alpha']
```

`beta` declared no dependency on `alpha` and received `alpha`'s output anyway, because the
runner handed every stage the whole of `produced`. Run sequentially, that is invisible and
harmless — the order is fixed, so the extra input is always there. Run the two stages at
once and `beta` sees `alpha` or does not, depending on which coroutine finishes first.

Saturating a GPU is a performance problem. An answer that depends on scheduling is a
correctness problem, and parallelism would have introduced it silently into a module whose
whole subject is provenance.

## Decision

**A stage receives exactly what it declared it needed.** `needs` was always the contract;
it is now also the mechanism. What a stage sees is a function of its declaration and not of
when it ran, which is what makes running stages at once safe rather than lucky.

**The count that bounds a machine is Thursday's own.** `DeviceCapacity` records how many
jobs *this process* has dispatched to each machine and not had back. That number is exact,
current, and exists before any heartbeat could carry it.

It is used two ways, and the distinction is the design:

* **The router's use is a preference.** `in_flight` enters the score ahead of the telemetry
  `idle`, because a count of what we just sent is strictly better information than a
  snapshot of what the machine last said. It is advisory: two stages can read it before
  either has taken a slot, and then both prefer the same machine.
* **The ledger's use is a guarantee.** `hold` is a per-device semaphore. A machine runs at
  most `limit` of Thursday's jobs at once, and a stage that would exceed it waits. Racing
  the preference costs a worse spread; it cannot cost saturation.

Splitting them this way means the fast path needs no locking and the safety property needs
no luck.

**Independent stages run together.** The runner walks waves: the set of not-yet-run stages
whose declared inputs exist. Nothing topologically sorts — a wave uses only edges the
planner wrote down, and a cycle still surfaces as a wave that comes up empty with stages
left over, which is the planner's to catch (§53).

**The default limit is one.** A second heavy inference on the same GPU does not finish
sooner; it shares VRAM with the first, and on a card sized for one model it fails outright.
The gain from concurrency is *across* machines, which is what §21's example describes.
Nothing on a machine reports how many models it can serve at once, so there is no better
default to infer — `set_limit` and `device_concurrency` exist for an owner who knows.

**The cloud is never bounded.** A provider runs its own concurrency on hardware Thursday
does not own; a cap here would be inventing a limit nobody asked for. Spend and rate are
already governed at the router (§45). This is about not flattening a machine in the house.

## Consequences

Measured after, same two machines, three independent stages, limit 1 each:

```
STAGES LANDED ON: ['machine-A', 'machine-B', 'machine-A']
DISTINCT MACHINES USED: 2 of 2 available
THREE INDEPENDENT STAGES TOOK: 0.40s   (was 0.60s; longest alone = 0.20s)
PEAK CONCURRENT DISPATCHES: 2  | per-machine limit: 1
LEDGER AFTER: empty (every slot released)
```

0.40s and not 0.20s is the limit working. Two machines, one job each at a time: two stages
overlap and the third waits. A number closer to 0.20s would have meant three inferences on
two GPUs, which is the thing this ADR exists to prevent.

Checked by reverting each property in turn:

| Mutation | Red |
|---|---|
| Router stops counting its own dispatches (telemetry only) | 1 |
| A stage is handed output it never declared | 1 |
| Executor never holds a slot | 1 |
| Runner back to one stage at a time | 2 |

**What this does not do.** A slot is held for the duration of the work, and an inference
that never returns holds its slot until it does. There is no timeout here because there is
none in `ComputeExecutor` either, and putting the deadline in the ledger would place it in
the wrong layer: whether to abandon a stage is the executor's question, not the accountant's.

The ledger is per-process and in-memory, like the rate limiter (ADR 0043). Two API workers
would each keep their own count and each permit `limit` jobs on the same machine. For the
single-owner desktop deployment this document describes that is the whole system; for
anything else it is a stated limit rather than a solved problem.

A stage that reads an input it never declared now fails where it used to work. That is the
point, and it is the only behaviour change a caller can observe.
