# ADR 0007 — Node commands are namespaced (`file.read`, not `read_file`)

**Status:** accepted · **Date:** 2026-09-01

## Context

V1 used flat command names. V2 specifies dotted namespaces. The obvious reading is
cosmetic — it is not.

## Decision

`<domain>.<verb>[.<qualifier>]`: `file.read`, `system.process.stop`, `audio.volume.set`.

## Consequences

- The permission matrix keys on a prefix. `file.*` defaults to MODIFY; `system.*` to SYSTEM.
  A new command inherits a sane default instead of falling through to "unknown", and the
  fail-closed path stops being the common path.
- Capability advertisement becomes a tree: a node can advertise `file.*` without enumerating
  seven verbs, and the hub can refuse `file.delete` on a node that advertised only `file.read`.
- Agent tool allowlists become expressible as prefixes, which is how they are actually
  reasoned about ("this agent may read files but not write them").
- **Cost we accepted:** a rename across the catalogue, the executor, the tool registry and
  the agents. A compatibility alias map keeps old names resolving for one release.
