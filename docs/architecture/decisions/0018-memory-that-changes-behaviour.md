# ADR 0018 — A memory that never changes what Thursday does is a note

**Status:** accepted · **Date:** 2026-09-02

## Context

Before V5 the memory system could store a preference, rank it well, and hand it to the
context package — and then nothing read it. The owner could say "these reports start with a
summary table", watch Thursday agree, and get the same report as before. Asked what it
remembered, it would recite the instruction correctly.

That is the difference between a second brain and a notebook, and it is not a small one:
the owner who takes the trouble to state a preference has to state it again every time,
which teaches them not to bother.

The related failure is subtler. `MemoryLayer` had six members, but the write path mapped
everything that was not a preference to `SEMANTIC`. A procedural memory — *how* to do the
work — was therefore indistinguishable from trivia about the world, and the layer that
exists to be applied was never populated.

## Decision

Three connected changes.

**Layer the write correctly.** The intent rules classify a "remember that…" statement as
procedural when it describes how work should be done, and the engine maps every layer the
rules produce rather than collapsing to `SEMANTIC`. "ให้สรุปเป็นตาราง" is an instruction
for next time; "my office is room 402" is a fact.

**Apply procedures at plan time.** `Planner._apply_remembered_procedures` reads the
procedural and preference memories already in the context package and attaches them to the
steps that *produce* something — document, data, design, media, coding. Not to every step:
telling a file search to start with a summary table is noise, and noise in an objective is
what makes an agent do the wrong thing.

**Record what is being followed.** `Plan.following` carries the instructions, so the owner
can see *why* the output looks the way it does and correct the memory rather than the
output. A prompt injection that never appears in the record would be invisible; an
instruction that does appear can be argued with.

Two guards: a memory below `PROCEDURE_MIN_CONFIDENCE` is not applied, because acting on a
guess about how someone wants their work done is worse than asking; and at most four apply
at once, beyond which guidance becomes a second prompt.

## Consequences

- Retrieval scoring now carries project relevance as a *soft* term. Asked how these reports
  are usually written, this project's answer comes first — but a general habit is still a
  real answer, and a hard filter would hide the thing that shaped it.
- Source confidence is weighted by provenance (`SOURCE_TRUST`). The owner stating something
  outranks an agent's inference about the same subject even when the inference is more
  confident in itself: confidence measures how sure a source is, not how much it is worth
  believing.
- **Cost we accepted:** applied procedures make output less predictable from the request
  alone — the same sentence produces different work depending on what Thursday remembers.
  That is the feature, and `Plan.following` is what keeps it inspectable rather than
  mysterious.

## Alternatives considered

- **Inject memories into the model prompt and hope.** What most assistants do. Rejected:
  it leaves no record of what was applied, so a bad memory produces bad output with no
  visible cause, and the owner debugs the wrong thing.
- **Apply to every step.** Rejected — see the noise argument above.
- **Ask before applying.** Rejected: the owner already said it once. Asking again is the
  failure this whole ADR exists to fix.
