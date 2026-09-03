# 46. A privacy rule expressed as a score is a preference

Date: Sprint 56 (ADDENDUM — Local AI Compute)

## Status

Accepted. Builds on [0044](0044-discovery-that-does-not-scan.md) and
[0045](0045-a-report-is-an-observation-a-correction-is-a-decision.md).

## Context

The Compute Router picks which machine and which model. The addendum gives it a long list of
inputs (§7): privacy, model requirements, latency, device availability, GPU load, VRAM, RAM,
network latency, power state, cost. §46 adds six profiles to weigh them differently, §15 adds
five modes, §45 lets the owner override.

Written the obvious way, that is a scoring function. Each input contributes points; the
highest total wins; profiles change the weights. It handles every requirement in the list, and
it is one line of arithmetic away from a serious failure.

Consider a SECRET document, a local GPU box that is thermally throttling with nine jobs
queued, and an idle cloud model. Every input except one points at the cloud. If privacy is
worth points — even a great many points — then the question of whether the document leaves the
machine has become a question of how the weights were tuned, and the answer changes when
somebody tunes them. The rule survives only until an unrelated adjustment to load weighting
outruns it.

## Decision

**Exclusions filter. Preferences rank. A score can never resurrect an excluded candidate.**

`_excluded` runs first and returns a *reason* rather than a penalty. SECRET never reaching a
cloud provider (§10), HIGHLY_PRIVATE staying local (§32), LOCAL_ONLY sending nothing out
(§16), a model that does not fit the hardware (§5), a machine thermally throttling or with no
free VRAM under heavy work (§18), a laptop on low battery (§19) — all of these remove a
candidate from consideration entirely. Only what survives is scored.

This also makes the two kinds of "no" legible to the reader, which the scoring version does
not: **unsuitable** (a busy machine — try later, or try another) and **forbidden** (a cloud
provider holding a SECRET payload — never, under any load, in any profile).

**The score is a tuple, not a sum.** `(explicit, locality, gpu, warm, idle, speed)` compares
left to right, so the ordering is readable and no weight can be quietly tuned into dominating
another. Profiles reorder the tuple rather than re-weighting a sum.

**The owner's explicit choice (§45) outranks preferences and not exclusions.** Asking for a
machine that cannot hold the model does not make it fit. The instruction is respected among
candidates that qualify — which is what "respect explicit routing instruction" can mean
without becoming "override physics".

**Zero tokens/second means unmeasured, not slow.** §25's benchmark has not run, so the column
is zero for everything. Reading that as "slow" is self-fulfilling: the unmeasured model is
never chosen, so it is never measured, so it stays unmeasured for ever.

**Load rules apply only to heavy work.** Refusing a one-line answer because a laptop is
unplugged, or because a GPU is briefly at 95%, costs more than it saves. §18 and §19 are about
work large enough for the machine's state to matter.

**The fallback chain is filtered by the same exclusions.** A fallback that was never checked
against the privacy rules is a way around them — the first failure would route the SECRET
document to the cloud candidate that `choose` correctly refused. Every step of the chain
passed the same filter.

**An empty result raises with the reasons.** §38 says a routing failure must not be silent.
`NoComputeAvailable` carries why each candidate was rejected, which is what turns "Thursday
cannot" into something the owner can act on — usually "the GPU box is asleep" or "no vision
model is installed anywhere".

## Consequences

`GET /compute/route` answers the question §44 says the owner should never have to ask, for the
times they ask anyway. Routing is the kind of logic that goes subtly wrong for months without
anybody noticing, and a router whose decisions cannot be inspected is one nobody can debug.

The router decides **where**, never **whether**. §30 and §31 are unchanged: a local model has
no more authority than a cloud one, an agent cannot self-authorize, and the Permission Engine
still gates every action the model proposes. Nothing in this file consults or modifies a
policy.

What is not built yet: the router names a cloud candidate but nothing executes against it —
`choose` is a decision, and Sprint 57's fallback path is what runs it. Escalation tiers (§13)
are expressible with the existing profiles but are not yet automatic: nothing yet retries a
low-confidence local answer against a stronger model (§14), and the honest statement is that
Tier 0–5 is a policy this router can serve rather than one it currently implements.
