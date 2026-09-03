# 48. Sending is not waking, and measuring changes what runs next

Date: Sprints 60–61 (ADDENDUM — Wake-on-LAN and benchmarks)

## Status

Accepted. Follows the six compute sprints (ADRs 0044–0047).

## Context

Two capabilities the addendum lists and the first six sprints deliberately left out:
Wake-on-LAN (§20) and model benchmarking (§25–§26).

They look unrelated. They share a property: both produce a number or a claim that something
downstream *acts on*, and in both cases the obvious implementation produces a confident answer
it has not earned.

## Decision — Wake-on-LAN

**Sending a magic packet is not evidence that anything woke.** It is an unacknowledged UDP
broadcast. A machine that is unplugged, has WoL disabled in firmware, or sits behind a router
swallows it in exactly the same silence as one that is booting. Reporting success from having
sent is the `verified: true` lie ADR 0012 exists to prevent, and it is worse here than usual
because the caller's next step is to route work to a machine that may not exist.

So waking is an ACT → VERIFY pair like every other device action. The packet goes out; then
the core waits for **the node to connect**, which is an observation it already makes. No node,
no success — `sent` and `verified` are separate fields because they are separate facts.

**Waking is a physical act, so the Permission Engine decides.** It draws power, spins fans,
lights a room, and may be happening at three in the morning beside somebody asleep — none of
which Thursday can perceive, which is precisely why it is not Thursday's call. §20 says
"request approval/policy check" and `device.wake` is `ASK_ALWAYS` to begin with, for the reason
§102 and §104 give for their own categories: a new capability with a physical consequence
starts by asking, and the owner lowers it once they know what it does.

**A MAC address is configuration, never discovery.** Recorded when the owner sets it, not
learned from traffic. Thursday sniffing MAC addresses would be the reconnaissance ADR 0044
refused for inference endpoints, and a magic packet sent to an address Thursday guessed is a
packet aimed at somebody else's machine. Having an address and consenting to use it are also
separate: `enabled` defaults to false, so recording where a machine is does not by itself
authorise waking it.

## Decision — benchmarks

§25's four words are the whole brief: *use real data to adjust routing*. That makes this
measurement with a feedback loop, and the loop is what every decision below is about:
**a bad number changes which model runs next, and a model that stops running is never measured
again.**

**Measured from real calls, not a benchmark harness.** A benchmark prompt measures a prompt
nobody asked for, on a machine in a state nobody was in. Real calls already happen and already
have a stopwatch on them. The cost is that an unused model stays unmeasured — which is honest,
and which the router already handles by reading zero as *unknown* rather than as slow.

**The median, not the mean.** One call during a backup, a thermal event or a suspend/resume
moves a mean by an order of magnitude. Routing then moves away, the model stops being called,
and the outlier becomes permanent. A median needs half the window to be bad before it moves.

**A cold model is not a slow model.** §22 already tracks LOADED/UNLOADED. The first call after
a model is paged in from disk measures the disk, and a forty-second first token would make a
good model look unusable for as long as the window remembers it. Cold samples are recorded and
kept out of the speed figure.

**A failure is only the model's if the model failed.** An unplugged machine, a dropped socket,
the owner disabling a model mid-flight — none are evidence about the model. Counting them
would let one bad afternoon on the network permanently demote the best model in the house,
invisibly, because the number looks like a measurement. Faults are classified, and
infrastructure failures are excluded from *both* halves of the success rate rather than
counted as successes. They are still reported, because an operator needs to see a flaky
machine.

`UNKNOWN` is not a free pass: an unclassified failure counts against the model, or an
unclassified path would quietly launder every failure it produced.

**Unmeasured is not bad.** A model with no history reads as zero, and zero is never treated as
a low score — otherwise the first model ever measured would win for ever, because nothing else
ever gets a chance to be measured.

## Consequences

`GET /compute/benchmarks` reports each model with `measured: true|false`, so an operator can
tell a slow model from one nobody has used. The QUALITY profile now ranks on success rate
ahead of hardware (§26): the point of that profile is the answer, and a GPU is a means to it.

**A bug the release gate found while tightening.** The gate's "every action has a policy" check
inferred *missing* from the resolved policy's shape — `ASK_ALWAYS` + `MEDIUM` outside a
known-strict namespace. `device.wake` is deliberately `ASK_ALWAYS`/`MEDIUM`, so the check
reported a policy that exists as missing. Replacing the heuristic with a direct question to the
table then surfaced a real gap it had been hiding: the old version exempted every `system.*`
action, and `system.process.start` had no policy of its own. It was resolving safely through
the namespace default, but by accident. It is now stated — stricter than `system.process.stop`,
because stopping a process ends something the owner can see and starting one runs code they
have not read.

What is not built: no scheduled or predictive waking (nothing wakes a machine because a
calendar entry suggests it will be needed), and benchmarks are in-memory, so a restart forgets
what was measured. The `models` table has `tokens_per_second` and `last_benchmarked_at`
columns ready for it; persisting them needs a decision about whether a measurement from before
a hardware change should survive, and guessing that is worse than restarting the window.
