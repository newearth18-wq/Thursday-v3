# ADR 0002 — PostgreSQL + pgvector as a single datastore

**Status:** accepted · **Date:** 2026-09-01

## Context

Memory needs vector similarity *and* relational integrity: a memory has a source, a
confidence, a project, a supersession link and an audit trail. The obvious alternative is a
dedicated vector database (Qdrant, Chroma, Pinecone) alongside Postgres.

## Decision

One datastore: PostgreSQL 16 with pgvector, HNSW index on `memories.embedding`.

## Consequences

- A memory and its metadata are written in one transaction, so they cannot drift apart.
  With two stores, every write is a distributed transaction nobody actually implements, and
  the failure mode is silent: an embedding whose row was rolled back still answers queries.
- Retrieval can filter on `project_id`, `layer` and `confidence` in the same query as the
  similarity search, instead of over-fetching and filtering in Python.
- **Cost we accepted:** pgvector is slower than a specialised engine at millions of vectors.
  A personal corpus is thousands. If that changes, `VectorProvider` is already the seam.
