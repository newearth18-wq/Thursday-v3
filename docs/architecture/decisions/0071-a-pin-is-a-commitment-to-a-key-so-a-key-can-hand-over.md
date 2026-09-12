# 71. A pin is a commitment to a key, so a key can hand over

Date: Sprint 102

## Status

Accepted. Closes the last rotation §117 asks for, and the one §23 has been naming as open
since ADR 0067 landed.

## Context

Device keys rotate (ADR 0042). The shared enrolment token rotates (ADR 0066). Provider API
keys rotate (ADR 0067). The core's TLS key did not, and §23 said why every sprint since:

> a node pins the core's SubjectPublicKeyInfo, learned at pairing where a person was present
> (ADR 0041), so rotating it invalidates every node's pin at once. That needs a signed
> hand-over from the retiring key, which is a different design rather than a longer window or
> a verified swap.

That is the problem exactly. A node refuses any key but the pinned one — that is what makes
pinning worth having — so replacing the certificate's key stops every machine in the house at
the same instant, each needing somebody to walk to it and pair it again. The two shapes that
worked for the other rotations do not apply. A **window** (ADR 0066) needs both parties to
agree on when, and a node that was switched off for it is still stranded. A **verified swap**
(ADR 0067) works because one party holds the key; here the other party is the thing that has
to be convinced.

## Decision

**A pin is `sha256(SubjectPublicKeyInfo)` — a commitment to a public key.** So a statement
that *carries* the retiring SPKI can be checked against a pin by anyone holding only the pin,
and once the hash matches, the verifier has the real public key and can check a signature made
by its private half.

That is the whole mechanism. A hand-over is three signed fields:

| field | why it is in the signature |
|---|---|
| `retiring_spki` | lets a holder of nothing but a pin check who signed |
| `next_pin` | so a captured signature cannot be re-pointed at a successor of someone else's choosing — the lesson `rotation_payload` records about `new_public_key` |
| `issued_at` | orders a chain, and gives the owner a date to recognise |

There is deliberately **no hostname and no expiry**. A field inside a signature that the
verifier does not act on is decoration, and decoration in a signed payload is the thing a later
reader mistakes for a check.

### Following it grants nothing new

Whoever holds the retiring private key could impersonate the core to a node pinned to it
anyway — that is what a pin *means*. A hand-over signed by that key lets them redirect the node
instead, which is the same power reached a different way. That equivalence is what makes
following safe, and it is why a chain is safe too: walking A→B→C ends somewhere only a holder
of A could have sent the node, and a holder of A could have sent it there in one step.

### No window, because nobody has to be awake

The statement is a permanent fact rather than a permission that expires, so a node follows it
whenever it happens to notice — which is **the first time it fails to connect**, on a
`PinMismatch`. It fetches the published chain, verifies it against the pin it already holds,
re-pins, and reconnects. A node in a bag for six months and two rotations walks the chain when
it comes back out. Nothing expires, so nothing bricks a machine that was switched off at the
wrong moment.

Following is **lazy and self-proving**: the node moves its pin and immediately re-checks it
against the live connection, so a hand-over that named a key the core is not actually serving
fails at once instead of being believed.

It asks **once per pin**. A node whose pin genuinely does not match reconnects for as long as
it runs, and re-fetching every time would be a node attacking its own core on the strength of a
failure.

### The endpoint is unauthenticated, and has to be

`GET /devices/tls-handover` takes no credential. The caller is a node that could not open the
channel it would authenticate on — that is the entire situation. Nothing served is secret:
public keys and the dates they changed. And it cannot be abused by serving lies, because every
link is verified against the pin the node already holds, and a chain the core's key did not
sign moves nothing. The core does not validate the file it serves either, deliberately: the
only verification that means anything happens on the node, and a check at the core would invite
a reader to think one had been done for them.

## Consequences

**Thursday does not hold the core's TLS key.** It does not generate, store, install or rotate
it — the core is served behind whatever terminates TLS for it, and that key is the operator's.
So the signing tool is `python -m apps.server --sign-handover`, run by hand on the host by
whoever holds the retiring key, and it touches no network. This ADR does not add a rotation to
Thursday; it makes a rotation the operator performs survivable for the nodes.

**A compromised key cannot be handed over, and the tool refuses to pretend otherwise.** Turn
the safety argument around: if somebody else holds the retiring key, they can sign a hand-over
too, and each node follows whichever reaches it first. Rotating away from a stolen key is a
race, and the nodes that lose it are pointed at the attacker by their own pin. So
`--compromised` refuses and writes nothing, and says the only thing that actually removes the
old key's authority rather than racing it: **re-pair every node with a person at the machine**,
which is the same anchor the pin was taken from in the first place. An operator can of course
answer the question wrongly; that is disclosure, not enforcement, and it is the same shape as
ADR 0067's refusal to claim it revoked a key at a provider.

**A fork is refused where it is still fixable.** Two hand-overs signed by the same key send
different nodes to different successors, and a node takes the first branch it finds. That does
not fail loudly — it strands whichever machines walked the abandoned branch, and the symptom
weeks later is "some of them never came back". The tool refuses to write the second one. The
node stays tolerant rather than policing the core's file: every link it walks is verified, so
the worst a fork costs it is a failure to connect.

**The chain is bounded at sixteen links.** It arrives from an unauthenticated endpoint, so its
length is somebody else's choice, and the work has to be bounded before it starts.

**The previous pin is kept on the node, and nothing reads it back.** It is written because
"what did this machine trust, and when did that stop" is a question the file should answer
without a log that has rotated away.
