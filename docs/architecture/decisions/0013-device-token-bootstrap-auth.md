# ADR 0013 — The node's HELLO is signed, and the signature is checked

**Status:** accepted · **Date:** 2026-09-02

## Context

The device node is the component that runs commands on the owner's real machine and reports
whether they worked. Both halves are attack surface, and the second is the worse one.

An impostor node that registers itself can be told to open an application — that is bad. But
it can also report `verified: true` for an action it never took, and nothing downstream has
any way to doubt it. Verification (ADR 0012) is the property everything else rests on, and
it is worth exactly as much as the identity of whatever is doing the verifying.

The implementation before this ADR checked that `Hello.signature` was non-empty and never
checked its value — and it closed the socket only when `environment == "production"`. Every
development and staging deployment trusted anything that connected.

## Decision

HMAC-SHA256 over the identifying fields of the HELLO, keyed by a shared enrolment token
read from the environment, compared with `hmac.compare_digest`, and enforced in **every**
environment.

- The signed payload is `device_id|name|os|nonce|issued_at`. Signing only the nonce would
  let a captured HELLO be re-presented under a different device name.
- The frame's own timestamp must be within five minutes of the core's clock, and its nonce
  must not have been seen inside that window. A captured HELLO is stale before it is useful.
- The nonce memory is bounded, so a node reconnecting in a loop cannot grow it without limit.
- A core that requires signatures and has no token configured refuses everything. Reading
  "required, but unconfigured" as "allow all" is how an unauthenticated device ends up
  trusted in production.
- The REST enrolment endpoints use the same check. A second door into the trusted-device set
  would be worth exactly as much as the weaker of the two.

This is **bootstrap** authentication. The token authenticates *a* node, not *this* node:
anything holding it can register under any name.

## Consequences

- Running a node requires setting `THURSDAY_SECRET_DEVICE_ENROLLMENT_SECRET` on both sides.
  The node refuses to start without it and prints the command to generate one.
- The token is read from the environment, never from a flag — a token on the command line
  lands in shell history and in every `ps` listing on the machine — and never logged.
- The node's identity file holds only its device id. It used to hold a random secret that
  the core never checked; that secret authenticated nothing and is now dropped on load.
- **Cost we accepted:** one shared secret across all of the owner's machines, and no
  per-device revocation. For a personal system with a handful of machines that is a
  reasonable first step, and it is a large improvement on no check at all.

## Alternatives considered

- **Ed25519 per-device keypairs.** The intended end state, and the `device_credentials`
  table already models it. Deferred because it needs a pairing flow (showing a code on the
  core, confirming it on the device) that is its own piece of work. Moving to it changes
  `DeviceAuthenticator.verify` and nothing else — not the protocol, not the callers.
- **mTLS.** Stronger, and it moves the problem to certificate distribution on a personal
  Windows machine, which is worse ergonomics than the thing it replaces.
- **Trust the network, bind to loopback.** Rejected: it collapses the moment the owner wants
  a second machine, which is the entire point of a device node.
