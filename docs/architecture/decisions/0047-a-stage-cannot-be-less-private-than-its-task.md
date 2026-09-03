# 47. A stage cannot be less private than its task

Date: Sprint 59 (ADDENDUM — Local AI Compute)

## Status

Accepted. Completes the addendum's six compute sprints, with
[0044](0044-discovery-that-does-not-scan.md),
[0045](0045-a-report-is-an-observation-a-correction-is-a-decision.md) and
[0046](0046-a-privacy-rule-expressed-as-a-score-is-a-preference.md).

## Context

§21 splits one task across the house: the GPU box does vision, the server does embeddings, the
laptop preprocesses files, cloud handles hard reasoning. §12 is the same idea as a pipeline —
OCR, embedding, summary, then *cloud reasoning only if needed*.

Each stage is routed separately, and it has to be: `ai.vision` and `ai.embedding` have
different candidates, different hardware needs and different machines that can serve them.
Routing the task once would put embeddings on the GPU box because that is where the vision
model happens to live.

Separate routing raises a question the single-machine case never had. A stage carries its own
inputs — an image, a vector, a paragraph. What sensitivity should each be routed at?

## Decision

**`max(stage.sensitivity, task.sensitivity)`. A stage may raise the floor and never lower it.**

The tempting answer is to judge each stage on its own inputs, and it fails quietly. Take a
SECRET document: the OCR stage handles an image, the embedding stage handles a vector, the
summarising stage handles a paragraph. Each looks defensible in isolation — a vector is not
obviously a secret, and a paragraph of a document is not the document.

But every one is *derived from* the secret. An embedding of a passphrase reaches the same
place the passphrase would, and is roughly as useful to whoever receives it. A pipeline that
routes each stage on its own declared sensitivity leaks the document one derivative at a
time, with every individual decision looking reasonable in review.

So the task's classification is a floor. A stage can declare itself *more* sensitive than its
task — that is a stage being careful — and cannot declare itself less.

**Provenance is part of the result.** §28's `AIJobResult` carries the device and the model,
and `DistributedResult` keeps all of them. "Thursday answered" is not an adequate account of a
task that touched four machines: the owner is entitled to know which, and an operator
debugging a wrong answer needs to know where it came from. `summary()` is what the owner is
shown.

**Required and optional stages fail differently.** §12's "cloud reasoning only if needed"
means a stage nobody depends on failing is a *less complete* answer. A required stage failing
is different in kind: a summary built without the OCR that was supposed to read the document
is not a worse answer, it is a wrong one. Optional stages degrade, required stages raise, and
both record what happened.

**Dependencies are declared, not inferred.** `needs` names other stages; the runner checks
them and refuses to run a stage whose inputs never arrived. It does not topologically sort its
input — that would hide the plan from the planner that produced it, and §53 puts cycle
detection in the planner where it belongs.

## Consequences

Distribution comes almost free from the previous three ADRs. Routing per stage is
`ComputeRouter.choose` per stage; safe fallback per stage is `ComputeExecutor.run` per stage;
the privacy floor is one `max`. There is no separate distributed-privacy mechanism, which is
the outcome to want — a second mechanism is a second thing to keep in agreement with the
first.

**A bug this sprint found in the last one.** Writing the "public task may use the cloud for
the stage that needs it" test showed the OCR stage going to a cloud provider while a local OCR
model sat idle on the laptop. The router's scoring used a dict lookup whose default was
*prefer cloud*, so `AUTO` — which is `ComputeRequest`'s own default — preferred the cloud.
Deployments were unaffected because the container's configured default is `LOCAL_FIRST`, and
that is exactly why it survived Sprint 56's twenty-eight tests: the wrong branch was never the
one the settings took. Only `CLOUD_FIRST` and `CLOUD_ONLY` prefer the cloud now, and a
regression test names the case.

What this does not do: run stages in parallel. §21's example is naturally concurrent — vision
on one machine while embeddings run on another — and this executes in the order given. The
plan's `needs` graph has the information required to parallelise, and doing so needs a
concurrency limit per device (§129) that does not exist yet, so it is left undone rather than
half-done.
