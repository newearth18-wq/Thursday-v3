# ADR 0041 — The node authenticates the core, too

**Status:** accepted · **Date:** 2026-09-03 · **Completes:** [ADR 0029](0029-a-paired-device-leaves-the-shared-token-behind.md)

## Context

Sprint 36 gave every node an Ed25519 identity and made the core check it. Auditing that work
for TLS pinning surfaced something the sprint had not said out loud: **the authentication runs
in one direction.** The node proves who it is. The core proves nothing.

The consequence is not obvious from either end, which is why it survived. A node is the
component that runs commands on the owner's real machine. Whoever it believes is the core gets
to drive it. An attacker who can obtain a certificate for the core's hostname — a mis-issuing
CA, a corporate middlebox, a compromised registrar — can sit between them, accept the node's
HELLO, and then send it whatever the node is capable of doing. The node's own key is no help
at all: it authenticates the node *to the impostor*.

Ordinary TLS does not close this, because the thing being trusted is the public CA set, and
the public CA set is exactly what the attacker has.

## Decision

**Pin the core's SubjectPublicKeyInfo, learned at pairing, checked on every connection.**

**SPKI rather than the certificate.** A certificate rotates on renewal; the key underneath it
usually does not. Pinning the certificate breaks the connection every ninety days on a Let's
Encrypt deployment, and a pin that breaks routinely is a pin somebody switches off. There is a
test that renews a certificate with the same key and asserts the pin is unchanged.

**Learned at pairing, because that is where the human is.** Trust-on-first-use is only as good
as the moment it happens. Pairing is the one moment in this system where a person is standing
at the device confirming a code (ADR 0029), so anchoring the pin there costs nothing extra and
is far better than a blind first connection. The pin's short form is printed next to the
pairing code, so the owner can compare it.

**Checked before the HELLO goes out.** A HELLO handed to an impostor is a HELLO an impostor
can relay. There is a test asserting the check precedes the send in `_session`.

**A recorded pin cannot be silently dropped.** A node holding a pin refuses a plaintext
connection outright — a node that connects in the clear has had its pin removed by whoever
chose the URL. This is the same reasoning that stops a paired device authenticating with the
shared token: a defence with a fallback is worth what the fallback is worth.

**`pinned_context` turns off chain validation because the pin replaces it.** That is only
sound because `check_peer` runs afterwards, so the caller that builds the context must be the
caller that checks. Stated in the docstring and asserted in a test, because a context built and
then not checked is *strictly worse* than the default one — it is the shape of a real mistake.

**Unreachable is not a mismatch.** `PinUnavailable` and `PinMismatch` are different types
because an operational problem and an attack call for different responses, and collapsing them
would train somebody to ignore the second.

**No pin is a supported configuration.** A LAN deployment on plain `ws://` records none and
behaves as before. What must not happen is a node believing it has a pin when it does not.

## Consequences

- The device channel is now mutually authenticated: the node by its Ed25519 key, the core by
  its pinned key.
- Replacing the core's certificate *with a new key* requires re-pairing the nodes. That is the
  intended cost — it is the same event as "somebody is presenting a different key", and the
  refusal message says so and names re-pairing as the deliberate fix.
- **Verified against a real TLS handshake**, not a mocked socket: the tests start a local
  TLS server with a generated certificate and connect to it. The claim is about what happens
  on the wire, and a mocked socket would only prove the mock agreed with the code.
- **Cost we accepted:** the pin is recorded only when pairing happens over `wss://`. A
  deployment that pairs over plain HTTP on a LAN and later moves to TLS has to re-pair to gain
  a pin. Recording a pin from an unencrypted pairing would be recording nothing.

## Alternatives considered

- **Have the core sign its WELCOME with a key the node learns at pairing.** Stronger in one
  respect — it works over plain `ws://` on a LAN, where there is no certificate to pin — and
  rejected for now because it is a protocol change on both ends, where pinning is a client-side
  addition that closes the same hole. Worth revisiting: it would make the LAN case as strong as
  the TLS one.
- **Trust the system CA store and check the hostname.** Rejected: that is the assumption being
  attacked.
- **Pin the whole certificate.** Rejected — see above; it breaks on every renewal.
- **Learn the pin on first connection rather than at pairing.** Rejected: it is the same
  mechanism with a strictly worse anchor, and the better anchor was already there.
