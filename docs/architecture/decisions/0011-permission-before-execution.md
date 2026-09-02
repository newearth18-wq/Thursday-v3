# ADR 0011 — Permission is decided before execution, in one place

**Status:** accepted · **Date:** 2026-09-02

## Context

Every component that can act is a component that can act wrongly: an agent following a
plan, a tool invoked directly, an automation firing on a schedule, the REST API called by a
script. If each one carries its own idea of what is allowed, then "may Thursday delete
this?" has as many answers as there are callers, and the weakest one is the real policy.

The bootstrap brief states the requirement plainly — *permission before execution* — but
the interesting question is not whether to check. It is where the check lives, and what
happens to a caller that forgets it.

## Decision

One `PermissionEngine.decide(ActionRequest) -> PermissionVerdict`, called by
`ToolExecutor` on the single path every action takes. Not by the agent, not by the router,
not by the endpoint.

Three properties follow from putting it there:

- **A caller cannot skip it.** There is no execute path around the executor. Adding one
  would mean adding a second way to run a tool, which is a reviewable change, not an
  oversight.
- **The decision precedes the side effect.** The verdict is computed, and only then is the
  tool called. An approval prompt shown while the file is already gone is theatre.
- **The default is refusal.** An action the policy table does not recognise resolves to
  `ASK_ALWAYS` at `MODIFY` level, not to `AUTO`. A new verb someone adds next year is
  cautious until a human says otherwise.

The `BLOCK` set has no override path at all: not by configuration, not by a standing
grant, not by raising the autonomy level, and not by an agent's own reasoning about why
this case is different.

## Consequences

- The engine is on the hot path of every action, so it must be fast and total. It is pure
  computation over the policy table, grants and world state — no I/O.
- Testing the security model does not require running an agent. `tests/unit/test_permissions.py`
  drives the engine directly.
- **Cost we accepted:** a genuinely harmless new tool still needs a policy entry before it
  can run unattended. That friction is the mechanism working: the alternative is a system
  where forgetting to think about a tool's blast radius is the same as deciding it has none.

## Alternatives considered

- **Checks inside each agent.** Rejected: it puts the security decision in the component
  with the strongest incentive to proceed, and every new agent re-litigates it.
- **A decorator on tool functions.** Rejected: silently unenforced on any tool whose author
  forgets it, and invisible when auditing what the rules actually are.
- **Post-hoc audit only — act, then flag.** Rejected outright for irreversible actions. An
  audit log that records the deletion of a file is not a control.
