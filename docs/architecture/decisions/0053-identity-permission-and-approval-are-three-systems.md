# 53. Identity, permission and approval are three systems

Date: Sprints 73–79 (BIOMETRIC IDENTITY, OWNER RECOGNITION & ACCESS CONTROL)

## Status

Accepted. Extends [0011](0011-permission-before-execution.md) by putting something *before*
it, and [0012](0012-verification-before-completion.md) by treating liveness as the observation
that makes a match mean anything.

## Context

The biometric requirement opens with a separation, not a feature:

> IDENTITY — who is giving the instruction · PERMISSION — what that person may do ·
> APPROVAL — whether this action happens now. *"สามสิ่งนี้ห้ามรวมเป็นระบบเดียวกัน."*

Systems merge these because merging looks tidier: one call, one verdict. What it produces is a
number that means neither thing. Relaxing an identity threshold silently widens a permission,
and nobody can answer "what may a guest do" without also reasoning about cameras.

The requirement's other governing sentence is §90's warning: Thursday must know its owner and
must not believe merely because it saw a similar face, heard a similar voice, or is on the
usual machine. Each of those is a resemblance an attacker can arrange, and each is
individually convincing.

## Decision

**The Permission Engine gains nothing.** No `auth_level` parameter, no `user_id`, nothing about
identity inside `ActionRequest`. An `IdentityGate` runs *before* it and refuses, so an
under-authenticated request never reaches the engine and the engine never has to be careful
about one. `GateVerdict` deliberately does not share `PolicyDecision`'s vocabulary — sharing it
is how two systems start being treated as one. Tests walk all three of those signatures.

**Liveness is a separate axis from confidence, everywhere.** A photograph of the owner matches
a template *perfectly* — better than the owner at an angle in bad light — so matching harder
makes the attack easier. Blending the two into one score ranks a good photograph above a real
face. Liveness is scored by *kind* of evidence rather than amount: blink, head movement and
temporal variation are all things a phone playing a video produces, so they are capped below
the threshold however many are present, and only challenge-response, depth or infrared break
through.

**Levels count independent kinds of evidence and never sum confidences.** A 0.99 face and a
0.99 voice are the same two things more confidently, not more independent things. Summing would
let one excellent factor buy what two mediocre independent ones could not — backwards, because
one excellent factor is one photograph away from wrong. An AST check asserts nothing in the
fusion engine calls `sum`/`mean`/`average` over confidences.

**Ceilings are imported, not decided.** Voice as the only biometric never passes
`VOICE_ALONE_CEILING` (§17); no biometric at all never passes `DEVICE_ONLY_CEILING` (§36). Both
live in the module where the spec sentence is quoted, so raising one means editing that line.

**With no recogniser, Thursday refuses rather than pretends.** `NoFaceRecognition` and
`NoSpeakerRecognition` authenticate nobody, following `NoKeychain`. A stub returning a
plausible 0.9 would be a security hole shaped like a feature: whoever deployed it would believe
they had face recognition and would have a lock that opens for everybody while reporting itself
locked. The speaker stub reports replay and synthetic risk as **1.0**, because "I cannot tell
whether this is a recording" is much closer to "it might be" than to "it is not".

**Recovery is deterministic, and it exists.** §45 forbids a model deciding somebody "seems
like the owner" — a model asked that will sometimes say yes to somebody persuasive, and
persuasion is what an attacker brings. So every path compares against something stored, no
method takes free text, and a test walks the imports to assert there is no model to ask. §46
and §47 forbid the opposite failure just as firmly: a system that locks the owner out of their
own machine because their voice is hoarse has not been secure, it has been uninstalled.

**Presence expires, and degrades before it locks.** Silence is absence, not continuity — a
camera that stopped reporting looks exactly like the owner sitting still. But §24 is a real
requirement: a system that re-challenges constantly is one people switch off. So a momentary
step out of frame applies a *ceiling* (ordinary work continues, private work stops) and only
confirmed absence locks.

## Consequences

Four defects, each found by running the thing rather than reading it.

The guest check read `risk > RiskLevel.LOW`. `RiskLevel` is a StrEnum, so that compares strings
and `"HIGH"` sorts below `"LOW"` — the clause meant to stop a guest deleting files blocked
MEDIUM and let HIGH and CRITICAL through. The enum module warns about this in its own comment.

`REQUIRED_FOR_RISK` omitted `RiskLevel.NONE`, so the fail-closed default made a risk-free action
demand the strongest identity.

Presence dropped straight to level 0 on any absence while the module docstring described a
gradual degrade, and the test asserted `< STRONG`, which passes at zero. "Degrade" and "lock"
were the same event and §24 had no implementation.

And the one the §89 acceptance tests were written to find, which no unit test could have:
**an observed biometric that matched nobody was discarded as absence of evidence.** A stranger
sitting at the machine playing a recording of the owner had their mismatching face silently
dropped and was admitted on the voice alone — §64's exact attack. `IdentityClaim.observed` now
distinguishes "nothing was looked at" from "somebody was looked at and it is not them", and the
second contradicts. A related dishonesty surfaced with it: the matchers named the owner at any
confidence above zero, so a 0.05 match asserted "this is the owner". One `USABLE_CONFIDENCE`
now governs both naming and counting.

What is **not** proved: this container has no camera, no microphone, no Windows Hello and no
depth sensor. Every frame and sample in the tests is a Python object and every provider returns
a number a test chose. The policy is tested and composes; whether a real recogniser can tell two
people apart is untested and stays untested until this runs on hardware. That gap is the reason
the absent-provider classes refuse rather than approximate.
