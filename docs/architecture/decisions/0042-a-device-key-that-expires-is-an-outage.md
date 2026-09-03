# 42. A device key that expires is an outage; a session that never does is a hole

Date: Sprint 52

## Status

Accepted. Extends [0013](0013-device-token-bootstrap-auth.md),
[0024](0024-a-command-that-crosses-machines.md) and
[0041](0041-the-node-authenticates-the-core-too.md).

## Context

§117 asks for credential rotation and §79 for short-lived sessions. Neither existed. The
schema had carried `device_credentials.rotates_at` since §8 and nothing had ever written it.

Sizing the gap turned up three defects rather than one missing feature, and they were the
same story — a device session authenticated once and then never questioned again:

1. `NodeSession.close()` dropped the session from the hub and left the socket open. Revoking
   a connected device therefore did not disconnect it. §134's emergency stop calls the same
   method to "disconnect Nodes", so the kill switch disconnected nothing a node could notice.
2. A HELLO authenticated a connection for as long as it stayed up — days, on a machine that
   never sleeps.
3. Nothing rotated at all.

A fourth surfaced while testing: `DeviceAuthenticator.verify` refused outright when no shared
enrolment token was configured, *before* it looked at the device's registered key. The
deployment §80 aims at — every machine paired, the enrolment token dropped because it has no
job left — refused every properly paired device.

## Decision

**A device key does not expire. A device session does.**

Rotation is authorised by the key being replaced, not by a person. Pairing needs somebody
standing at the machine reading a code off a screen, which is the right price for enrolling a
device and the wrong price for hygiene: if rotating means walking to every machine, nobody
rotates and §117 stays a paragraph. Whoever holds the current private key already *is* the
device as far as the core can tell, so letting them name a successor grants nothing they did
not already have.

Two signatures over one payload. The retiring key's signature is the authority. The incoming
key's signature proves the node can actually use what it is asking the core to adopt — not a
security property, since an attacker with the old key could sign both, but the difference
between a rotation that fails and a rotation that bricks the machine. The payload binds the
device id, the **old fingerprint** and the **new public key**, so a captured request cannot be
replayed against a later credential or have a different key swapped into it.

Revocation still wins. A revoked credential cannot rotate, however good its signatures;
otherwise revocation would have a door in it, opened with the key that was revoked.

**Age is reported, never enforced.** `rotation_due` appears on `GET /devices/credentials` and
changes nothing about whether the device works. An expiring device key would convert "the
owner was away for a while" into "every machine is locked out and must be re-paired by hand" —
the outage `credentials.py` was written to prevent, arriving on a timer instead of on a
restart. A control whose failure mode is an outage that only a physical visit fixes is a
control that gets switched off.

**Sessions do expire**, at twelve hours by default, with no way to configure away the bound —
`Settings` refuses a lifetime under fifteen minutes. This is what makes rotation mean
something: a session authenticated with a key that has since been replaced would otherwise
outlive that key.

Two close codes, deliberately distinct. `4408` means the session aged out — reconnect now.
`4409` means the core ended it — revocation, or the emergency stop. A node that reads a
routine expiry as a refusal is a machine the owner silently loses; one that reads a refusal as
an expiry hammers a core that will never accept it.

**The enrolment token is only for enrolment.** A device with a registered key is judged by
that key whether or not a token is configured. Failing closed still applies where it should:
no key *and* no token means nothing to check a signature against, and the device is refused.

## Consequences

Rotation is a single command on the node (`--rotate-key`) and needs no person at the machine.
The successor key is written to disk before the core is asked to take it, so the one failure
that would otherwise cost a physical visit — the core accepts, the reply is lost — becomes a
retry: the node asks the core which key it holds and promotes only on a match.

Revocation and the emergency stop now do what they say at the transport level. Every node
reconnects at least twice a day, which is a small cost on a link that is up and a real one on
a link that is not — hence the fifteen-minute floor rather than a smaller one.

What this does **not** do: rotate the shared enrolment token, the core's own TLS key, or any
provider API key. §117 lists four rotations and this implements one. The others are named in
`docs/23-release-readiness.md` rather than quietly folded into this ADR's claim.

Nor does it start writing the `device_credentials` table. The registry is still the JSON
credential store of ADR 0013, and `rotates_at` in the schema is still written by nothing — the
new field is `rotated_at`, on the store, which is a different question (when it last changed,
not when it next should). The near-identical names are a trap, and the honest statement is
that the table remains unused by the pairing service rather than that this sprint filled it
in.
