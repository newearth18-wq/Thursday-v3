# ADR 0023 — The conversation knows which machine, and says so

**Status:** accepted · **Date:** 2026-09-02

## Context

    (on the phone)  "Thursday คอมที่บ้านเปิดอยู่ไหม"     → about Home-PC
    (on the phone)  "เปิด Chrome ให้หน่อย"                → ?

The second sentence names no machine. Before V8, Thursday resolved it to the device the
owner was speaking from and opened Chrome on their phone.

That is not a failed command. A failed command is visible: something does not happen and
somebody notices. This is a command that *succeeded*, on the wrong machine, and was
reported as success — the owner learns about it when they walk into the other room, if ever.
The device router's own docstring already called acting on the wrong machine "one of the
least forgivable mistakes an assistant can make", and the router was doing it.

The immediate cause turned out to be upstream of the router. `ReasoningEngine._anchor`
filled in `target_device = "this"` whenever an utterance named no device. Downstream, "the
owner named no device" and "the owner said *this machine*" are different facts that the
router treats differently — the second is an explicit instruction that should override
anything inherited. Manufacturing the second from the first destroyed the distinction
before any router logic could use it.

## Decision

**A device that gets named becomes the subject of that conversation** (`thursday_core.focus`).
It applies when the next sentence names no machine, and it is bounded three ways:

- **It expires.** `FOCUS_TTL_SECONDS` is three minutes — long enough for the follow-up
  someone actually says next, too short to reach across a change of subject.
- **It is per conversation.** A conversation on the laptop cannot steer one on the phone.
- **An explicit word always wins**, in both directions: naming another machine moves the
  focus, and saying "เครื่องนี้" clears it.

**And Thursday says which machine it used.** `DeviceResolution.announce` is set whenever
neither the sentence nor the owner's own machine chose the target, and the reply then names
the device. This is the half that makes the feature safe rather than merely convenient: an
action landing somewhere the owner did not name, silently, is the failure being prevented,
not one worth introducing. Naming the device on *every* reply would be noise, and noise is
what stops people reading the one reply where it mattered — so it is said exactly when it
was not obvious.

**`_anchor` no longer invents a hint.** Silence is a meaningful state and the router already
handles it; the default added nothing but ambiguity.

## Consequences

- Cross-device commands become easy, which is the feature — and is precisely why
  [ADR 0024](0024-a-command-that-crosses-machines.md) exists. The two were designed together
  and neither is complete alone.
- `PlanStep` carries `resolved_device` and `device_announced`: what the owner said and what
  actually happened are different facts, and after the fact only the second one matters.
- **Cost we accepted:** a focus that is occasionally wrong. The owner asks about the home PC,
  changes the subject inside three minutes without saying so, and the next command goes to
  the wrong machine — where it is announced, and is undoable. The alternative costs the
  feature entirely.
- Task recall (`"ผลเมื่อกี้เป็นยังไง"`) is deliberately not session-scoped, because the
  owner asking is usually on a different device than the one that did the work. Tasks live
  in the core precisely so this works; scoping them to their originating session would make
  the core's ownership of tasks pointless.

## Alternatives considered

- **Always ask which machine.** Rejected: correct, and unusable. "Which device?" after every
  sentence is how people stop using a feature.
- **Use the world's `active_device_id` as the focus.** Rejected: it answers a different
  question — where the owner *is*, not what they are talking about — and those differ in
  exactly the cases that matter.
- **Make the focus permanent for the conversation.** Rejected: a device named at the start
  of a long conversation would silently capture an unrelated command an hour later.
