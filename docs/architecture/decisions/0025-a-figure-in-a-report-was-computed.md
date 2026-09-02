# ADR 0025 — Every figure in a report was computed, and the report proves it

**Status:** accepted · **Date:** 2026-09-02

## Context

V9's headline is an analysis DAG: read a file, compute statistics, write a report. The
tempting shape is to hand the table to a model and ask for the report. It produces good
prose and it is the wrong design, for a reason that only shows up at the worst moment.

A model asked for an average returns a plausible number. A plausible number that is wrong
survives every check except the one nobody ran — it looks right, it reads right, it is in a
document someone forwards to a head of department, and the error surfaces weeks later when
somebody recomputes it by hand. The failure is silent, confident, and lands in the artefact
with the longest life of anything Thursday produces.

Two concrete failures during V9 made the shape of this precise.

**The report that contained none of its analysis.** Running offline, the model returned its
standard "I cannot answer analytical questions right now". That is a non-empty string. The
report step's criterion was `output.document is not empty`. It passed. The owner received a
document shaped like a report, titled like a report, containing none of the figures that had
just been correctly computed for it — and every check in the Supervisor said PASS.

**The schema the planner guessed.** A `data` step carrying a `question` argument was checked
against the *research* agent's output schema and failed for missing `findings` it had never
claimed to return. The planner names a step and hopes an agent exists to fill it; only the
agent knows what it produces.

## Decision

**Arithmetic is arithmetic.** `DataAgent` computes every figure in Python from rows it can
point at, and returns the rows (`output.rows`, `output.count`) alongside them so
`Supervisor._check_arithmetic` can confirm the count matches and the percentages sum to 100.
The model is used only for the sentence at the end, over figures that are already fixed.

**`DocumentAgent` assembles, it does not calculate.** Figures come from `contract.upstream`,
the model is given them as fixed text, and `output.sources` names which step each part rests
on.

**A report must carry its figures.** `thursday_agents.grounding.grounded` asks one question
with a cheap exact answer: did any computed number reach the prose. It is a substring test
and deliberately crude — it cannot tell a good report from a bad one and does not try. Where
the model's text is not grounded, both agents fall back to a plainer output built from the
figures, which is true by construction. `output.grounded` is a declared field, so the
Supervisor checks it rather than checking emptiness.

**An agent declares its own output schema.** `AgentSpec.output_schema` wins over the
planner's inference, which stays as the fallback for agents that declare nothing.

**Steps pass their outputs downstream.** `JobContract.upstream` carries what a step's
dependencies produced, keyed by step name. Without it a plan is a sequence, not a DAG: the
gather → analyse → report chain ran in the right order and the analyse step never saw the
data. It is a separate field from `inputs` so an agent can always tell what the planner asked
for apart from what another agent handed it — the second is a result, and results are wrong
sometimes.

## Consequences

- Thursday can produce a correct report with no model available at all. The offline fallback
  is plain and short and every number in it is real, which is the property that matters for
  an artefact someone acts on.
- The Supervisor's arithmetic checks became reachable. They existed before V9 and nothing
  produced the `percentages`, `count` and `rows` keys they read.
- **Cost we accepted:** the reports are duller than a model writing freely would produce.
  A duller report that is right beats a fluent one that is not, in a document whose whole
  purpose is to be acted on.
- `DataAgent` and `DocumentAgent` hold no tools. They work on what an earlier step already
  read, so an analysis cannot widen into a file read nobody planned, and a report writer
  cannot overwrite a file.

## Alternatives considered

- **Ask the model and validate the numbers afterwards.** Rejected: validating an arbitrary
  number against a table is the same work as computing it, done later and less reliably.
- **Require the model to emit structured figures.** Rejected: it moves the trust from prose
  to JSON without removing it. The figures would still originate in a generation.
- **Check the report with a second model call.** Rejected — an LLM judging an LLM's
  arithmetic fails in correlated ways, costs another call, and is unavailable exactly when
  the first call was (offline), which is when this failed in the first place.
