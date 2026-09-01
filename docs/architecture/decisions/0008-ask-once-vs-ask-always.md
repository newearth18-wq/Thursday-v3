# ADR 0008 — `ASK_ONCE` and `ASK_ALWAYS` are different policies

**Status:** accepted · **Date:** 2026-09-01

## Context

V1 had a single `ASK`. When the user answered "always allow", a scoped grant was written for
any asked action. That is right for `file.move` and badly wrong for `file.delete`.

## Decision

Four policies: `AUTO`, `ASK_ONCE`, `ASK_ALWAYS`, `BLOCK`.

`ASK_ONCE` may produce a scoped, expiring grant. `ASK_ALWAYS` may never produce a grant
under any scope, and the approval UI does not offer the option.

## Consequences

- Deleting, sending, purchasing, installing and elevating are asked *every time*, forever.
  No sequence of hurried approvals can quietly turn them into standing permissions.
- The distinction is enforced in `ApprovalService`, not in the UI, so an API client cannot
  route around it by passing `scope=always`.
- **Cost we accepted:** more prompts for genuinely repetitive risky work. That is the
  intended trade: the alternative is an assistant that gradually acquires the ability to
  delete things unattended.
