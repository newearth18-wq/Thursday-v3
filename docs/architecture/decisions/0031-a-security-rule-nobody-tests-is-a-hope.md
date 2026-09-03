# ADR 0031 — A security rule nobody tests is a hope

**Status:** accepted · **Date:** 2026-09-02

## Context

By Sprint 46 the V12 security specification (§76–135) had been implemented across a dozen
sprints, and its rules were documented in module docstrings, ADRs and the threat model. What
none of that establishes is whether the rules still hold. A property asserted in prose
degrades silently: the code that enforced it gets refactored, a new namespace is added
beneath a hardened one, a call site that used to redact stops being on the path — and nothing
fails, because nothing was checking.

Writing the suite found three places where that had already happened.

## Decision

**The absolute rules of V12 are executable tests**, in `tests/integration/`, organised by the
section each rule comes from — so a failure names the rule that was violated rather than the
function that changed. They run against the *built container* and the real policy table,
because every one of these is a claim about the assembled system, and a claim about an object
constructed inside the test is a claim about the test.

Three fixes fell out of writing them.

**Policy resolution walks to the nearest listed ancestor.** `PolicyTable.get` matched exactly,
then fell through to single-segment namespace defaults. It never consulted the table for an
intermediate prefix. So `file.delete.bulk` — which nobody had listed — did not inherit
`file.delete`'s ASK_ALWAYS/HIGH; it landed on the `file` namespace default of ASK_ONCE/MEDIUM.
The more dangerous action carried the weaker policy, and "always ask before deleting" was one
naming convention away from being bypassed. ADR 0007 established prefix-walking for the action
vocabulary; the policy table had simply never used it. An inherited policy keeps the parent's
level, decision, risk and reversibility: a sub-action is a narrower case of its parent, and
there is no reason a narrower case of "always ask" should ask less.

**Prompts are redacted at the router.** §90 lists the prompt transcript first among the places
a secret may never appear, and §194 states it again as a rule. The redaction module's own
docstring said it ran on every prompt. Nothing on the path to a provider called it. It runs
there now — every call, the local model included, because a secret does not stop being one
because the model is on this machine, and the prompt reaches a log line either way. What is
logged is the *name* of the pattern that matched; a log line that reports what it redacted has
not redacted it.

**Procedural memory is owner-only.** The guard against an agent or a document writing the
owner's preferences covered `MemoryLayer.PREFERENCE` and not `MemoryLayer.PROCEDURAL` — the
layer built in V5 specifically to *shape later work*. A web page could write a standing
instruction that changed how Thursday behaved for months without ever announcing itself, which
is exactly the substitution §110 forbids. Both behaviour-shaping layers now require the owner
as the source; anything else becomes a question. `PROJECT` is deliberately not in that set: a
project memory is a fact about a project, and agents are supposed to record those.

## Consequences

- The rules are now falsifiable, and three of them were false.
- `file.*`-style namespaces can be extended without each new verb needing its own hardening —
  inheritance is the default and the direction is strict.
- Redaction costs a regex pass per prompt. The trade-off is the redactor's own and is the
  right way round: a false positive costs one redacted string, a false negative costs a
  credential in somebody else's pipeline.
- An agent that wants to record how the owner likes things done must ask. That is slower, and
  it is the difference between learning and drifting.
- **Cost we accepted:** these tests are stricter than the code strictly needs today, so some
  will fail on a change that is actually fine. That is the correct failure direction for a
  test whose subject is a security property.

## Alternatives considered

- **Keep the rules as documentation and review them periodically.** Rejected: that is what was
  happening, and it is how three of them stopped being true without anyone noticing.
- **List every dangerous sub-action explicitly instead of inheriting.** Rejected — it fails
  open by omission, and the omission is invisible. Inheritance fails closed by default.
- **Redact at each call site.** Rejected for the same reason metering moved to the router
  (ADR 0030): the call sites that need it most are the ones nobody remembers.
- **Grep the source for dangerous constructs.** Rejected after trying it: a text scan matched
  the word "curl" inside a docstring. A check that cries wolf at prose gets switched off, and
  one that cannot tell code from a comment cannot be trusted when it stays silent. The §120
  check walks the AST.
