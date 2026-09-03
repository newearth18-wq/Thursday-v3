# ADR 0026 — A learned skill is code nobody reviewed

**Status:** accepted · **Date:** 2026-09-02

## Context

    "Thursday ทำรายงานคะแนนแบบที่เคยทำ"

Nothing in that sentence is a skill's name. It says the kind of work and that there was a
previous time, and the second half is the real signal: "แบบที่เคยทำ", "แบบเดิม", "like last
time" are the owner saying they are not describing a new job, they are asking for one that
already exists.

Before V9 the sentence parsed as a research question and came back "I found nothing" — true,
useless, and with the skill sitting in the registry the whole time.

Making it work means turning a sentence into the execution of a stored workflow. That is a
larger step than it looks. A skill is a list of tool calls that Thursday *learned by
watching*, which is to say it is code the owner did not write and nobody reviewed, and V9's
job is to let it run without that becoming a way around every rule the rest of the system
enforces.

## Decision

**Retrieval is lexical, not semantic.** Character trigrams over the skill's name, tags and
description, for the same reason the memory layer uses them: Thai is written without spaces,
so "คะแนน" inside "ทำรายงานคะแนนแบบที่เคยทำ" is a real mention that word-splitting misses.
Not embeddings: a skill run is an *action* with steps and permissions, so the difference
between the right skill and a nearly-related one is not a difference of degree. Lexical
matching fails visibly and locally; a vector search fails plausibly, and needs a model
running to fail at all.

The comparison direction is chosen per field and getting it wrong makes the feature silently
useless — which it was, first attempt. A short **name** is asked "how much of you appears in
this sentence"; a **description** of comparable length to the sentence is asked the reverse.
The description is what carries cross-language matches: a skill called "School Grade Report"
is asked for in Thai, and its Thai description is the only thing the two have in common.

**Ambiguity is a question, not a pick.** Two skills within `DECISIVE_MARGIN` mean the
sentence does not identify one of them, however high both score. Running the wrong workflow
is worse than asking, because a workflow has steps and the wrong steps have already happened
by the time anybody notices.

**The lifecycle is not bypassable.** `plan_from_skill` converts only an ACTIVE skill. Draft
and testing exist precisely so a learned workflow can be examined before it touches real
data, and a converter that quietly ran a draft would remove the only thing they are for.
When a matching skill is not yet approved, Thursday says so rather than falling through —
quietly planning something else would do work the owner did not ask for while looking like
it did what they wanted.

**Supplied inputs cannot overwrite what a skill does.** They are merged *under* each step's
own arguments. A caller that could rewrite them could turn "read this file" into "delete
that one" while still calling it by the skill's trusted name.

**Composition produces a draft.** Three workflows the owner approved separately are not the
same thing as one workflow that does all three in sequence, and the second is what they would
be agreeing to. So a composition re-enters the lifecycle at the beginning, and only ACTIVE
skills can be composed — chaining a draft in would give it a way to run.

**A skill step names exactly one of `tool` or `agent`.** Inferring the kind from the name was
tried first: an unregistered tool and an agent job look identical, so a typo validates as an
agent step and a real agent step fails sandbox validation as a missing tool.

## Consequences

- "แบบเดิม" with no learned skill falls through to ordinary planning, where a remembered
  instruction shapes it (ADR 0018). The phrase means two things and which one applies depends
  on what Thursday actually knows; answering the wrong one would be answering a question the
  owner did not ask while ignoring the one they did.
- A demonstration converts to a **linear** chain, never a parallel one. A sequence is the only
  dependency structure honestly recoverable from watching, and guessing that adjacent steps
  were independent would reorder someone's workflow on no evidence.
- **Cost we accepted:** lexical matching misses a skill described in words the owner did not
  use. The failure is a "I have no skill for that" they can correct in one sentence, rather
  than the wrong workflow running to completion.
- `the usual` is matched only when nothing follows it. "in the usual place" is a location, and
  treating it as a skill marker hijacked a file search until a test caught it.

## Alternatives considered

- **Embedding search over skill descriptions.** Rejected, as above — and it would make skill
  retrieval unavailable offline, which is when the rule-based path matters most.
- **Let the model pick the skill.** Rejected: the model would be choosing which stored
  workflow to execute against real files, from a prompt that includes untrusted content
  (ADR 0010). Retrieval stays deterministic and inspectable.
- **Activate a composed skill automatically when all its parts are active.** Rejected — see
  above; the composition is a new grant of authority, not a restatement of old ones.
