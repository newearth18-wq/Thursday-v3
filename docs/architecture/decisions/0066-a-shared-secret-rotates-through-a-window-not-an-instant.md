# 66. A shared secret rotates through a window, not an instant

Date: Sprint 97

## Status

Accepted. Closes one of the three rotations §117 lists and §23 has carried as open since
Sprint 52: the shared node enrolment token. The core's TLS key and provider API keys remain
open, and §23 says so.

## Context

Device **keys** rotate (ADR 0042): a node replaces its own key, signed by both the retiring
and the incoming key, the old one stops working immediately, and a revoked device cannot
rotate its way back in. That works because the secret is *per device* — there is a moment at
which exactly one machine hands over, and it is the machine doing the handing.

The shared enrolment token has no such moment. It is one secret held by every node that has
not yet paired, and the obvious rotation is to change the environment variable and restart:

* every un-paired node is refused at once, mid-enrolment, with "the HELLO signature did not
  match" — which reads to its owner as a typo;
* nothing anywhere records whether the old secret is still in use, so the rotation is
  performed and then the owner waits to see what breaks.

That is why the gap stayed open. The rotation was not hard to implement; it was hard to
implement *honestly*.

## Decision

**A rotation is a window with a stated end, not an instant.**

    ┌── the incoming token, accepted from the moment it is set
    │
    │   ┌── the retiring token, still accepted until `retires_at`
    │   │
    ├───┴────────────────────────────►  and then refused, with the date in the reason

Four properties, each answering one of the ways this goes wrong:

**The retiring token expires, and the date is required.** A second accepted secret with no
end is not a rotation — it is two tokens, with the owner believing otherwise. `retires_at` is
mandatory whenever a retiring token is set, and a half-configured rotation raises at
**startup**, where a person is looking, rather than at the first HELLO months later.

**Every acceptance names the token that was used**, by fingerprint — SHA-256 truncated to
eight characters, enough to tell two secrets apart in a log line and useless for guessing
one. "Is anything still using the old token?" becomes a question the logs answer. That is the
property that makes the window safe to close deliberately rather than hopefully.

**A node that arrives after the window is told what happened.** Not "the signature did not
match", which sends its owner hunting for a typo, but the date the previous token stopped
working.

**Nothing generates a secret.** There is no token generator in the module and a test asserts
there is none. A secret the machine invented and wrote down is a secret with one more copy of
itself in the world, in a place the owner does not know about. The incoming token is
supplied; this code only decides which supplied values are live right now.

**A minimum overlap.** Below an hour the window is theatre: the owner cannot reach the
machines still holding the old token before it stops working, so the rotation happens whether
or not they did — the un-rotated behaviour with extra steps. There is no *maximum*: a long
overlap is the owner's call and their risk, and the fingerprint in the logs is how they
decide it is over.

**Rotating a token to itself is refused.** Not pedantry — this is what it looks like when
somebody sets the new variable to the old value, and it would report a rotation in progress
for ever while changing nothing.

## Consequences

A paired device is untouched by any of this. It is judged against its own registered key and
the shared token has no job for it, so a second live token during a rotation is **not** a
second way in for a machine that already has a key — asserted by test, because that is
exactly the shape a rotation could have weakened.

The replay defence spans both tokens: a nonce spent on one cannot be spent again on the
other. Both live tokens are tried even after one matches, so the time taken does not depend
on which secret was used; there are at most two, so the cost is bounded.

`DeviceAuthenticator(token)` still works exactly as before and every existing caller is
unchanged. A rotation is the same object with a window, not a different API.

**Still open, and named rather than implied:** the core's TLS key and provider API keys.
The TLS key is the harder of the two and not for want of effort here — a node pins the core's
SubjectPublicKeyInfo, learned at pairing where a person was present (ADR 0041), so rotating
it invalidates every node's pin at once. That needs a signed hand-over from the retiring key,
which is a different design, not a longer window. Writing it down here is better than a
partial implementation that appears to have solved it.
