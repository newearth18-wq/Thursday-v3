# ADR 0004 — The orchestrator is ours; agent frameworks are adapters

**Status:** accepted · **Date:** 2026-09-01

## Context

LangGraph, the OpenAI Agents SDK and similar frameworks offer agent loops, routing and
tool calling. Adopting one as the spine would save real work.

## Decision

The orchestration loop — plan, authorise, delegate, verify, remember — is implemented in
`thursday_core`. Frameworks may be used *behind* `AgentProvider`, for one agent at a time.

## Consequences

- The properties the brief treats as non-negotiable — permission check before every tool
  call, mandatory supervision, bounded informed retries, an audit row per action — are ours
  to guarantee. Frameworks have opinions about all four, and none of those opinions is
  "refuse to report success until an observation confirms it".
- Two frameworks can coexist, one per agent, without a rewrite.
- **Cost we accepted:** we maintain the loop, including the parts a framework would have
  given us free. Roughly 700 lines, and they are the 700 lines the product is actually about.
