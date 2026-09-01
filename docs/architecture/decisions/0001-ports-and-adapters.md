# ADR 0001 — Ports and adapters, with one DI container

**Status:** accepted · **Date:** 2026-09-01

## Context

The brief requires that the LLM, STT, TTS, vector store, agent framework and cloud services
can each be replaced without rewriting the core. That promise is easy to make in a document
and almost always broken in practice, because a single `import openai` in a core module is
enough to break it permanently.

## Decision

Every external capability is a `typing.Protocol` in `thursday_shared.interfaces`. Core
modules depend on those names only. Concrete adapters are constructed exactly once, in
`thursday_core.container`, from settings — no core module ever calls a provider constructor.

Every port ships with at least two implementations, one of which runs fully offline.

## Consequences

- Swapping a provider is a config change plus one adapter file.
- The test suite builds a container of fakes with the same attribute surface, so tests need
  no network, no credentials and no GPU.
- **Cost we accepted:** one extra indirection on every call, and a container that knows about
  everything. The container is therefore the one file that must stay boring and readable.
