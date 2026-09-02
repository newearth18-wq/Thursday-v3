# ADR 0029 — A paired device leaves the shared token behind

**Status:** accepted · **Date:** 2026-09-02 · **Supersedes in part:** ADR 0013

## Context

ADR 0013 gave every node one shared enrolment token and was explicit about the weakness: it
authenticates *a* node, not *this* node. Anyone holding it can connect as any machine, under
any name. That matters more here than in most systems, because a node is the thing that runs
commands on the owner's real hardware and then reports whether they worked — an impostor can
act, but worse, it can report `verified: true` for something it never did, and verification
is the property everything else in Thursday rests on.

Blueprint §80–83 replaces it with per-device keys. The flow it specifies is short —

    node generates a keypair → asks to pair → shows a code → owner confirms → key registered

— and the interesting decisions are all in what the parts are *not*.

## Decision

**Two proofs, and neither alone is pairing.** `pair/start` requires the node to sign its
request with the private half of the key it is offering (*proof of possession*), so nobody
can register a key they do not hold. `pair/complete` requires a person to read a code off the
device's screen and type it into a client they already trust (*proof of presence*). Possession
alone would mean any process that can reach the API can enrol itself, which is not pairing
but self-service; presence alone would let a confirmed code register somebody else's key.

**The code is not a credential.** It authorises one enrolment, for five minutes, and what
gets stored is the public key. A leaked code costs one pairing inside its lifetime; a leaked
long-term token costs everything for ever, which is the difference this whole sprint buys.

**Guessing is what gets counted.** A six-digit code is a million combinations, and that only
holds up if guesses are bounded. The limit is deliberately *across all codes*, not per code:
the codes an attacker guesses do not exist, so a counter hanging off a pending record would
never see them — it would look like a defence and be nothing. The cost is that somebody
spamming wrong codes can stop the owner pairing for ten minutes; pairing is rare and the
owner is standing at the machine, and an attacker who can guess without limit cannot be
waited out.

**Once a device has paired, the shared token is closed for it — permanently.** The core
decides which scheme applies from its own registry, never from anything the node says. A
paired device is judged against its registered key *even when that check fails*: falling back
would mean pairing improved nothing, since the token would still work for every machine. The
node enforces the same rule from its side and never retries with the token.

**Revocation is sticky and comes before the fallback.** A revoked device fails the key check
rather than dropping through to the token — revocation a shared secret can route around is
not revocation. The credential record is kept rather than deleted, because "revoked on
Tuesday" is a fact somebody needs and because a deleted record would let the device pair
again as though nothing had happened. Revocation also removes the device from the hub
entirely rather than marking it offline: an offline device is one that is coming back, and a
revoked one re-pairs under a new identity.

**The core names the device, not the node.** The device id is assigned at pairing. A node
that could choose its own could claim the id of the *server*, and the owner confirming a code
shown on their laptop would be registering an attacker's key against a machine they never
touched.

**A freshly paired device is `LIMITED`, not `TRUSTED`.** §80 ends with "device becomes
TRUSTED" and this stops one step short deliberately: ADR 0024 made driving *other* machines a
separate decision the owner takes per device, and pairing a laptop is not the same act as
authorising it to reach the server.

**The private key never reaches the core.** `PublicKey` and `PrivateKey` are separate types
and only the public half has a serialisation the API accepts; the node writes its key 0600
into a file separate from the config an operator is likely to open, paste into a bug report,
or sync to a backup.

## Consequences

- The bootstrap token survives as an *enrolment* path only, for devices with no key on file.
  A deployment that pairs every node stops needing it on those machines at all.
- A node that has started pairing signs with its key from that moment, before the owner has
  confirmed — so it is refused and retries until they do. The alternative, falling back to
  the token while waiting, would leave the weaker credential live during exactly the window
  pairing exists to close.
- Somebody who loses a laptop revokes one device rather than rotating a secret across all of
  them.
- **Cost we accepted:** pairing is a manual step per device, and a spammed code costs the
  owner one restart of it.

## Alternatives considered

- **Let the node keep using the token as a fallback when its key is refused.** Rejected: it
  reduces the scheme to the weaker of the two credentials, which is the token.
- **Per-code guess counters.** Rejected: they count the guesses an attacker does not make.
- **Trust the device fully on pairing, per §80.** Rejected — see ADR 0024. Two decisions that
  look like one are the kind of thing that gets granted once and noticed later.
- **Have the node choose its own device id.** Rejected: it turns the owner's confirmation
  into an authorisation for a machine they were not looking at.
