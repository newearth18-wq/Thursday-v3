# ADR 0003 — Dramatiq for background work, not Celery

**Status:** accepted · **Date:** 2026-09-01

## Context

Long-running work (agent execution, document processing, automations) must not block the
conversation. The brief names Celery or Dramatiq and asks for a reason.

## Decision

Dramatiq, on Redis.

## Consequences

- Far fewer moving parts: no separate beat scheduler, no result backend to configure, no
  serialisation surprises. For a single-user system that matters more than Celery's breadth.
- Middleware is a small, explicit pipeline — which is where retry limits and the
  "never retry a destructive action" rule belong.
- **Cost we accepted:** a smaller ecosystem and less operational literature than Celery.
  Mitigated by `QueueProvider`: the actors are thin wrappers over functions that are
  callable directly, so tests and the CLI run them in-process with no broker at all.
