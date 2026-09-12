# 70. A surface may only take what it can take back

Date: Sprint 101

## Status

Accepted. Answers the question ADR 0069 left open and named as needing its own decision:
what a phone may do *to* a machine, as opposed to what it may answer about one.

## Context

Two things were in the way, and the first was not about phones at all.

**§20's own headline scenario did not work from anywhere.** The multi-device document opens
with *"เปิด Chrome ให้หน่อย"* and *"ปิดเครื่องให้หน่อย"*. The second is `system.power`,
`system.power` is ASK_ALWAYS, and `POST /devices/{id}/actions` answered anything that was not
AUTO with **403**. So did locking a screen, which is ASK_ONCE, LOW and reversible. The
Permission Engine was saying *ask the owner* and the endpoint was hearing *no* — a refusal
where the engine had asked for a question. Eight of the catalogue's thirty actions were
unreachable that way, for every caller, and §23 did not mention it because nobody had tried.

**And then the phone question.** ADR 0069 settled approvals — a phone may approve, but may not
grant standing permission — and said device control needed deciding rather than answering by
analogy. It does, because the criterion turns out to be a different one.

## Decision

### A gated action is asked about, not refused

`/devices/{id}/actions` now raises the approval and returns **202** with its id — the shape
every other deferred decision in this API already uses. BLOCK is still 403: that is the engine
saying no, and there is nobody to ask.

Nothing runs without consent, which is what the endpoint's guarantee always was. What changed
is that the owner now gets asked instead of the caller getting turned away. The test covering
this was rewritten to assert *that* rather than the status code, because the old version would
have passed just as happily while the endpoint refused things the owner should have been asked
about — which is exactly what it was doing.

### What a phone may do to a machine

**Can the surface that took the action undo it?**

| | worst case if it was a mistake |
|---|---|
| lock the screen | type a password. Seconds, in person, nothing lost |
| wake a machine | a machine is on. Electricity, fixable whenever |
| sleep / restart / shut down | unsaved work is gone **now**, and the machine is then unreachable from the phone |

The third row is the rule. It is not that shutting down is high-risk in the abstract — a
desktop may do it, because the owner can walk to the machine. It is that from a phone there is
**no way back**.

The apparent way back is wake-on-LAN, and this is where the decision turns on something this
project already wrote down: §23 says plainly that **waking a machine has never woken a
machine**. The packet format, the policy gate and the confirmation are built and honest about
being unproven. Allowing a shutdown on the strength of an undo that has never been observed to
work would be precisely the kind of promise the rest of this repository keeps refusing to
make.

So: **lock and wake, yes. Power, no.** Shell, PowerShell and file deletion are not on the list
either, and the list is an **allowlist** — a verb added to the catalogue later is off the phone
until somebody decides otherwise, which is the safe direction for a list people forget to
update.

`system.power` is **shown and refused with the reason** rather than left off the screen. Same
choice as the workflow builder's trigger with no runner (ADR 0064) and the walkthrough's
control that is not on screen (ADR 0068): a control that is silently absent teaches nothing and
reads as a broken app.

## Consequences

The two halves meet: the phone asks for a gated action, the approval arrives at the top of the
same screen, and the owner answers it once — never "always", by ADR 0069. A phone that asked
for something and then reported it as done would be the one lie that matters here, so the
button says *waiting for your approval* and not *done*.

Like ADR 0069's rule, the phone allowlist is a **discipline in the client**, not a boundary the
core enforces, and for the same reason: the core cannot tell which surface a request came from
without trusting a header the client sets. Unlike ADR 0069's rule, it is not the only thing
standing there — `system.power` is ASK_ALWAYS for every caller, so the engine asks the owner
regardless. The phone rule stops the question being raised at all from a place where the answer
cannot be undone.

**What this does not do.** It does not let a phone start work, run a shell, or reach the
machine any other way. Voice remote, conversation, camera input and push notifications remain
unbuilt and named as such in §23 — each needs its own decision about what a phone is allowed to
do, and this ADR is the shape those decisions should take rather than a precedent for assuming
them.
