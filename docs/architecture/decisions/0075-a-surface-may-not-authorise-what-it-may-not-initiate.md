# 75. A surface may not authorise what it may not initiate

Date: Sprint 106

## Status

Accepted. Closes a hole ADR 0070 left open in its own rule, one sprint after making it.

## Context

ADR 0070 decided what a phone may do *to* a machine, on the criterion **can the surface that
took the action undo it?** Lock and wake, yes. Sleep, restart and shut down, no — the owner is
in another building, unsaved work is gone immediately, and the apparent undo is wake-on-LAN,
which §23 says has never actually woken a machine.

That rule was implemented on the **button**. `system.power` renders struck through, disabled,
with the reason beside it.

It was not implemented on the **approval**, and the two sit on the same screen. Measured
against the shipped code, with one pending `system.power`:

```
BUTTONS OFFERED: ["อนุมัติครั้งนี้","ปฏิเสธ","ล็อกหน้าจอ","ปลุกเครื่อง","ปิดเครื่อง"]
APPROVE CALLED WITH: [["a-power","once"]]
```

The phone refused to press the shutdown, and offered to authorise the identical shutdown two
inches above it.

**This is not an edge case somebody had to contrive.** `system.power` is ASK_ALWAYS for every
caller — that is ADR 0070's own finding, and the reason `/devices/{id}/actions` now returns 202
with an approval rather than 403. So an approval *is* the normal way a shutdown arrives: raised
by a desktop button, an agent, a workflow, or a schedule. The path ADR 0070 guarded was the one
nobody was going to use; the path it left open was the default one.

## Decision

**A phone may not authorise what it may not initiate.** The approval reads the same list the
button reads, so the two cannot drift into disagreeing about what this surface may do.

For a refused action the phone:

- **shows the approval in full** — the owner is entitled to know something is waiting on them,
  and hiding it trades one silence for another;
- **offers only *reject*** — the safe answer, and always available. An approval nobody can
  answer either way strands the owner rather than protecting them;
- **says where the answer belongs**, rather than leaving a missing button to be noticed — the
  same choice the struck-through shutdown button makes a few lines further down.

### Why this is a denylist when the button's is an allowlist

The asymmetry is deliberate, and it is about which direction each one fails in.

| | list | failing closed costs |
|---|---|---|
| initiating | allowlist | nothing — the owner uses the desktop |
| answering | denylist | the phone's entire reason to exist |

ADR 0070 was right that a verb added to the catalogue later should be **off** the phone until
somebody decides otherwise: the phone draws a small fixed set of controls, and a new one
appearing there unreviewed is the risk.

Answering is the opposite. §64 is *see what is happening, answer what is being asked*. An
allowlist there would make every action nobody had thought of unanswerable from a phone — which
does not make the owner safer, it makes the phone useless and teaches them to walk to the desk
for everything, including the approvals it handles well.

## Consequences

**The cost is stated rather than hidden.** A future action that strands the owner the way
`system.power` does is approvable from a phone until it joins the list. That is the price of
the phone being able to answer anything at all. It is the same list the button reads, so adding
it once fixes both surfaces — which is the property that makes the price acceptable.

**Still a discipline in the client, not a boundary the core enforces.** Unchanged from ADR 0069
and ADR 0070, and for the same reason: the core cannot tell which surface a request came from
without trusting a header the client sets. What this protects against is the owner in a hurry,
which is the realistic failure. It is not a defence against a stolen phone — for that, revoke
the device (§80).

**A rule implemented on one control is not implemented.** Worth recording as the general shape,
because this is the second time in three sprints the same thing has happened: ADR 0073 found an
authenticated HTTP surface beside a WebSocket that reached the reasoning engine with no
credential at all. Both times the guarded path was the obvious one and the open path was the
one the system actually used. A rule is about an *outcome*; the next question after writing one
is which other doorways reach that outcome.
