# 65. A document that cannot be checked will be wrong

Date: Sprint 96

## Status

Accepted. Extends the README count checks of Sprint 50 to the documents that describe what
Thursday can do and what it cannot.

## Context

This project's first rule is that it does not fake functionality. Its second, in practice, is
that it writes down what it did not build — §23 exists to name every gap, because "some
limitations apply" is how a gap becomes a surprise.

Both rules are enforced in code for the *software*. Neither was enforced for the *prose*, and
by Sprint 96 the prose had drifted in five places, every one of them in the flattering
direction:

| Claim | Reality | How long |
|---|---|---|
| "Thirteen specialist agents" — in three documents | eighteen registered | since V13 |
| `media` agent: READ, "Cannot edit them" | MODIFY, three editing tools | since V11 |
| §23: "against 2,151 tests" | 2,317 | since V11 |
| §23: "no text-to-speech" | eSpeak NG, twenty lines above in the same file | since V12 |
| README: "80 REST operations" | 126 | unknown |
| README: "62 in the desktop app" | 169 | since V11 |

The `media` row is the one that matters most, and not because it is the oldest. A reader
consults the agent bench to decide **what their machine is allowed to do**. That table said
an agent was READ-only and could not edit files, while the agent held a MODIFY ceiling and
three editing tools. Everything else on the list was a stale number; that one was a false
statement about a permission.

None of this was carelessness at the moment of writing. Each was true when written, and each
was invalidated by a later sprint that had no reason to look at that file.

## Decision

**Any number or capability claim in the documentation is checked against the running system,
or it is not written.**

Concretely, `tests/integration/test_release_readiness_v50.py` now asserts:

* The agent bench table lists **exactly** the agents the container registers — neither more
  nor fewer.
* Every row's ceiling matches that agent's real `permission_ceiling`.
* All three documents that state the size of the bench state the same number, and that number
  is the registry's.
* §23's test count and the README's agree, and the README's is already checked against the
  tree.
* The README's REST operation count matches what the application actually serves, counted
  from its OpenAPI document rather than from the router files — because what a reader is
  asking is what the server exposes.
* The README's desktop test count does not exceed the number of specs written.
* §23 does not **deny a capability the container has**. A table of phrases (`"no
  text-to-speech"`, `"no automation engine"`, `"no trading module"`) is checked against the
  built container, and a phrase that is live while the capability exists fails the suite.

That last one is the general form of the V12 mistake and the only one of these checks that is
about meaning rather than arithmetic. It is deliberately a small, explicit list rather than
anything clever: a heuristic that tried to parse prose for claims would fail in both
directions, and a check nobody trusts gets deleted.

## Consequences

**Counts are now a build-time fact, not a habit.** Adding an agent fails the suite until the
bench table gains a row with the right ceiling. Adding a route fails it until the README's
number moves. That is the cost, and it is the point: the alternative is a document that is
wrong for four sprints and nobody notices.

**The historical sentence is kept.** §23 now records that it used to claim "no
text-to-speech" and for how long, rather than quietly correcting itself. A document about
honesty that edits its own errors out is a worse document, and the record is the reason the
check exists.

**What this does not check.** Prose that is merely out of date rather than arithmetically
wrong — a paragraph describing a subsystem that has since been redesigned — is still on the
author. These checks catch numbers and a named list of denials, and saying so is better than
implying the documentation is now self-verifying in general.

**The gap nobody had written down.** V15 found that schedule triggers had never fired once,
in a document whose whole purpose is to list what does not work. §23 could not have named it,
because nobody knew. That is the limit of a status document and the argument for tests over
prose wherever a property can be expressed as one.
