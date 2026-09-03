# ADR 0024 — A command that crosses machines is a different command

**Status:** accepted · **Date:** 2026-09-02

## Context

[ADR 0023](0023-the-conversation-knows-which-machine.md) makes cross-device commands easy —
a sentence need not name the machine it lands on. That is the feature, and it is also the
reason this decision has to exist alongside it. A convenience that quietly widens what an
unattended device can reach is not a convenience.

The difference between a local and a remote command is not the action. It is that the owner
cannot see the result, and neither can anyone else who happens to be holding their phone.
Everything that made "run it automatically" acceptable — that a person is present, watching,
able to interrupt — is exactly what distance removes.

Before V8 nothing in the system could tell the two apart. `DeviceAction` recorded where an
instruction was going and not where it came from, so there was no question to ask.

## Decision

An instruction now carries its **origin**: `ToolCall.origin_device_id` → `ActionRequest` →
`DeviceAction`, stamped once in `AgentContext.call_tool` from the turn that started the work.
An agent cannot choose its own origin; that is the only reason any of the rest means
anything.

**Devices carry a trust level** (`TrustLevel`), and it answers one question: may an
instruction that arrives *from* this machine reach another one. Not "may Thursday act here"
— a shared tablet is a fine thing to display a recipe on and a poor thing to accept "wipe
the server" from. New devices enrol at `LIMITED` and cannot drive anything until the owner
says otherwise, and the level is never read from a node's own HELLO: a device asserting its
own trust level is a device granting itself permission.

**Two components, two questions, and they do not overlap:**

`RemoteCommandGate` (in the hub, on the one dispatch path) can only *refuse*, on facts that
no approval could fix — an unrecognised origin, an untrusted origin, an untrusted target, an
unencrypted link.

`PermissionEngine.decide` rule 4b decides whether an allowed remote action still needs the
owner's word. It sits **before** the standing-grant check on purpose: "I approved deleting
files while sitting at my PC" is not the same decision as "anything holding my phone may
delete files on my PC", and letting a grant satisfy a remote consequential action would turn
one local approval into a standing remote capability. It returns `ASK_ALWAYS` rather than
`ASK_ONCE` for the same reason — `ASK_ONCE` may be remembered as a grant, and a standing
grant is what must not exist here (ADR 0008).

**What counts as consequential is derived, not listed.** A remote action at or above
`PermissionLevel.MODIFY` needs confirming, from the level the action already declares. The
first attempt was a hand-written list of namespaces and it was wrong within the hour: it
named `file.delete` and `file.move` and missed `file.copy`, `file.rename`, `file.create`,
`clipboard.write` and `app.close`, every one of which writes to a machine the owner cannot
see. A short list survives for the other direction — things *below* MODIFY that still
deserve a question, such as `system.process.start`.

**Audit records both ends.** `AuditEntry.origin_device_id`, and the hub's own log line names
the origin alongside the target.

## Consequences

- The V8 acceptance flow requires the owner to vouch for their phone. That is not friction
  to be designed away; it is the decision being asked for, once, explicitly.
- `DeviceActionRefused` is distinct from `DeviceActionFailed` and is not retryable: nothing
  was attempted on the target machine, so "it failed" would be the wrong thing to say and
  retrying the wrong thing to do. The remedy is trust or an approval, and a second attempt
  supplies neither.
- Read-only actions are deliberately *not* confirmed when remote. "Is my home PC on?" raising
  an approval prompt every time would be caution in appearance only: prompts the owner learns
  to dismiss without reading are how a real prompt comes to be dismissed too.
- A plaintext socket from the loopback interface counts as protected. Traffic that never
  leaves the machine has no segment for anyone to sit on, and the alternative — certificates
  before anyone can try a multi-device flow locally — ends with the check turned off, which
  protects nothing.
- **Cost we accepted:** writing a file to another machine asks every time, forever, with no
  way to make it stop. For an action whose result the owner cannot see, that is the correct
  amount of friction.

## Alternatives considered

- **Trust the authenticated session and stop there.** Rejected: authentication answers "which
  device is this", not "may this device drive that one". The phone is genuinely the owner's
  phone in every case this guards against.
- **Escalate remote actions inside the hub.** Rejected: approvals are the permission engine's
  job, and two components deciding the same question is how they come to disagree (ADR 0011).
- **Ask on every remote action.** Rejected — see approval fatigue above. The prompt that
  matters is the one that is still read.
