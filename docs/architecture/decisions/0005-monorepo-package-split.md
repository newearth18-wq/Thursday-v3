# ADR 0005 — Monorepo split into `packages/`, one distribution

**Status:** accepted · **Date:** 2026-09-01 · **Supersedes:** the V1 flat-package layout

## Context

V1 used a single `thursday/` package with subpackages, arguing that a dozen separately
versioned distributions buy nothing until there are a dozen deployment targets. V2 specifies
the `packages/` / `services/` tree explicitly.

## Decision

Adopt the specified tree. Each `packages/<domain>/` contains `thursday_<domain>/`. One
`pyproject.toml` lists all of them as sources of a single wheel.

## Consequences

- The tree matches the brief, and package boundaries make the import rules (shared imports
  nothing; core imports shared only) checkable rather than merely documented.
- One install, one test run, no cross-package version skew — which independent
  distributions would introduce for a system that always deploys together.
- **Cost we accepted:** a one-time rename across every import in the codebase. Done at 17k
  lines, verified by the suite; at 100k lines it would have been a project of its own.
- If a package ever must ship independently, it already has a directory and a clean import
  name. Only its build metadata changes.
