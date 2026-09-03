# ADR 0006 — Offline implementations are retained as first-class, not scaffolding

**Status:** accepted · **Date:** 2026-09-01

## Context

With Postgres, Redis and Dramatiq as the production default, the SQLite store, in-process
event bus, in-process queue, hash embeddings and rule-based LLM could all be deleted as
transitional scaffolding.

## Decision

They stay, as supported implementations of their ports, selected by configuration.

## Consequences

- The test suite runs with no database, no broker, no credentials and no GPU. A safety
  property that can only be tested against production infrastructure is not, in practice,
  tested — and the safety properties are the point of this system.
- Offline mode (PART 1.3) is not a special code path bolted on later; it is the same code
  path with different adapters, exercised on every CI run.
- **Cost we accepted:** two implementations of several ports to keep honest. The port
  contract and a shared test suite per port keep them from diverging.
