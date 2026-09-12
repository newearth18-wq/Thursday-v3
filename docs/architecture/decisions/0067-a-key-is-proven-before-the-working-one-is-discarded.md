# 67. A key is proven before the working one is discarded

Date: Sprint 98

## Status

Accepted. Closes the last of §117's three rotations this repository can honestly close. The
core's TLS key remains open, for the reason ADR 0066 gives.

## Context

The enrolment token rotation (ADR 0066) solved a sharing problem: one secret held by many
machines, so the danger is cutting off the ones still holding the old value, and the answer
is a window.

A provider API key is the opposite shape. One party holds it — Thursday — so replacing it
cuts nothing off and needs no window. The danger is entirely that **the replacement does not
work**, and the ways it does not work are mundane:

* copied short, or pasted with a trailing newline;
* revoked at the provider before it was installed here;
* belongs to a different account than the one being billed.

Write it over the working key and Thursday has discarded the only credential that worked,
and finds out at the next thing the owner asks for — by which time the old value exists
only in the owner's clipboard history, if anywhere.

`AnthropicLLM.health()` could not help: it checked that a secret was *registered* and
reported `detail="ok"`, which is true of every one of the failures above.

## Decision

**Prove the incoming key against the provider before writing anything.** The order is fixed
and the whole module is about that order:

1. verify the incoming key — a real call, not a format check
2. keep the outgoing key, under its own handle
3. only then make the incoming key live
4. tell the owner what Thursday cannot do

**Step one is a real call.** A format check — right prefix, right length — is cheap and
passes for three of the four failures above. So `verify` is a callback: the probe belongs to
whatever knows how to talk to that provider (ADR 0001), and this module records only whether
it returned true. `AnthropicLLM.probe()` posts the smallest body the API accepts and counts
only a 2xx.

A network failure during the probe returns false, and the rotation is refused. That is the
safe direction: rotating over a flaky connection must not discard a working key. It is also
why the refusal says the old key still works rather than blaming the new one.

**Step four is the honest part, and the reason this ADR exists.** Thursday can change which
key it uses. It **cannot revoke the old one** — that is a button in the provider's console,
and nothing here can press it. A rotation that reported success without saying so would leave
the owner believing a compromised key was dead while it is still live and still billable. So
`Rotated.outstanding` carries that sentence and is never empty.

**The outgoing key is kept, not deleted.** A provider that accepts a key once and rejects it
a minute later is a real failure mode, and the owner's way back should not be "find the old
key again". `roll_back` restores it and clears the keep-handle, so a second roll-back refuses
rather than silently reinstating the same key.

**A key with surrounding whitespace is refused, not trimmed.** It is the commonest way this
goes wrong and invisible on every screen. Trimming silently would leave the owner's clipboard
and Thursday's stored value different, and the next rotation comparing against the wrong
thing.

**Rotating a key to itself is refused before the probe runs**, so a rotation that was never
possible does not spend a paid API call finding that out.

## Consequences

`health()` no longer says `"ok"` for a registered key. It says "a key is registered (not
checked against the provider)", because the old wording read as "the provider is working" and
was true of a revoked key. Reaching the provider on every health check would put a paid
network call behind `/health`, so the check stays cheap and the *wording* stops overclaiming —
`probe()` is there for when the question is whether the key actually works.

Nothing returns, logs or stores a key in the clear; the owner sees fingerprints, on the same
terms as `enrolment.py`. A test asserts the module contains no format check — no `startswith`,
no length comparison, no provider prefix — because that is the shortcut a later change would
reach for, and it would quietly undo the guarantee.

**What is not automated.** Thursday does not schedule rotations, does not generate keys, and
does not decide when a key is old enough to replace. Key age is reported and never enforced,
for the same reason device key age is (§23): a credential that expired on its own would cut
the owner off from their own provider on a timer.

**Still open:** the core's TLS key, and only that. A node pins the core's SubjectPublicKeyInfo
learned at pairing (ADR 0041), so rotating it invalidates every node's pin at once — a signed
hand-over from the retiring key, which is a different design rather than a longer window or a
verified swap.
