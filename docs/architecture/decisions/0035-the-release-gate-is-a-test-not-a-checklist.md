# ADR 0035 — The release gate is a test, not a checklist

**Status:** accepted · **Date:** 2026-09-02

## Context

Every sprint in this project found the same class of bug, and it is worth listing them
together because the pattern is the finding:

- barge-in was documented and never fired;
- `IntentKind.APPROVE` existed and nothing produced it;
- the skill matcher scored its arguments backwards and was silently useless;
- `data` and `document` agents were planned for and absent;
- `requires_backup` was threaded through three layers and read by none;
- the redactor's docstring said it ran on every prompt; nothing called it on that path;
- policy resolution did not inherit from a listed ancestor, so the more dangerous action
  carried the weaker rule;
- `MemoryLayer.PROCEDURAL` accepted standing instructions from a web page;
- a metrics fallback would have made every action read identically, with the endpoint
  returning 200 throughout.

None of these failed a test, because no test asked. Each was found by reading the code against
its own docstrings — a method that does not scale and does not survive the person doing it
losing interest.

A release checklist has the same weakness. Somebody ticks it once.

## Decision

**The readiness claims are a test file.** `tests/integration/test_release_readiness_v50.py`
asks, on every run, the questions that would have caught the bugs above:

- does everything the container declares actually get built?
- does every port still work with the offline adapters, so the "no infrastructure" claim is
  real rather than historical?
- does every action in the catalogue have a policy of its own, rather than falling to the
  fail-closed default — which is safe, useless, and produces the approval fatigue that is a
  safety failure in its own right?
- does every agent declare what it returns, so the orchestrator never guesses a contract?
- is every ADR numbered without gaps, indexed, and reachable, and does every internal link in
  every document resolve?
- has the README's stated test count fallen behind the number of test functions in the tree?

**`docs/23-release-readiness.md` states the position in prose and names every gap
individually**, because "some limitations apply" is how a gap becomes a surprise. Each entry
says what would close it.

**The counts are checked, not trusted.** The README's numbers drifted twice during
development — it claimed 534 tests and seven phases while the branch carried 762 and ten. A
number in a README is a claim a reader uses to judge everything else.

## Consequences

- The release gate cannot be passed by asserting it has been passed.
- Adding an agent, an action or an ADR now has a consistency obligation attached, enforced at
  the moment it is added rather than at review.
- Two real gaps closed while writing it: the `computer` and `browser` agents had no declared
  `output_schema` and were still relying on the orchestrator's fallback heuristic — the exact
  bug V9 introduced `output_schema` to fix, still live for two of thirteen agents.
- **Cost we accepted:** the test count check has a tolerance, and the ADR count is matched
  against an English word list. Both are slightly silly, and both catch the thing that
  actually went wrong twice.
- **What it cannot do:** prove Thursday is good, or catch a README that *overstates*. It
  proves the documentation is not lying in the direction it decays, which is the direction
  nobody checks.

## Alternatives considered

- **A release checklist in a document.** Rejected: that is what the module docstrings already
  were, and they were wrong nine times.
- **Ask pytest at runtime how many tests it collected.** Tried, and removed: it passes or
  fails depending on whether you ran the whole suite or one file, which is not a test. The
  static count of `def test_` functions is a true lower bound, because parametrisation only
  multiplies.
- **Fail the build on any undeclared agent output schema at import.** Rejected as too rigid
  for a field added mid-project; the test names the offenders and the list is currently empty.
