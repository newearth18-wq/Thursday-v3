# ADR 0009 — Autonomy and proactivity are separate settings

**Status:** accepted · **Date:** 2026-09-01

## Context

V1 had one dial: proactivity (OFF/LOW/NORMAL/HIGH), governing unprompted notifications.
V2 adds autonomy levels 0–3, governing how much Thursday may do without asking.

## Decision

Keep both, orthogonal.

- **Proactivity** gates *speaking*: may Thursday raise this notification now?
- **Autonomy** gates *acting*: may Thursday perform this action without an approval?

`AutonomyLevel.SUGGEST_ONLY` forces every non-READ action to ASK, whatever the policy table
says. Higher levels relax `ASK_ONCE` actions only — `ASK_ALWAYS` and `BLOCK` are unaffected
at every level, including the highest.

## Consequences

- The two real user preferences — "don't interrupt me" and "don't touch things without
  asking" — are expressible independently. Collapsing them forces a false choice: a user who
  wants a quiet but capable assistant would otherwise have to also make it timid.
- Autonomy can only tighten the policy table, never loosen it past `ASK_ALWAYS`. The most
  permissive setting is still not admin.
