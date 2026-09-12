# 69. A phone may approve, but may not grant standing permission

Date: Sprint 100

## Status

Accepted. Gives the phone the two things §64 asks a phone for — see what is happening, answer
what is being asked — and settles what it may *not* do while answering.

## Context

The Android app has existed since Sprint 87 and it is not a separate codebase: CI
cross-compiles `apps/desktop` into an `.apk` on every commit, and the frontend it loads is the
same React app the desktop window runs (ADR 0057). What it had never had was a layout for a
phone, so what the owner got at 400px was a desktop window: a nav pinned to `bottom-6 left-8`,
a 21rem drawer over a 900-unit canvas, all of it overlapping.

Meanwhile `apps/mobile/README.md` described a Flutter app — framework, directory layout,
wiring plan — for a client nobody was building, while the real one shipped from somewhere
else. §23 called the mobile client "a scaffold", which was generous: the directory held one
stale README and no code.

Adding a phone layout is small. The question it forces is not.

## The question

The owner is away from the desk and something needs approving. The phone is where they are
reachable — that is the entire argument for approvals on a phone (§38). So the phone must be
able to say yes.

But `ApprovalScope` has two values, and they are not the same kind of answer:

* **ONCE** answers a question the owner can see described in front of them.
* **ALWAYS** is a durable grant of authority that outlives the moment.

And the moment is the problem. A phone approval happens away from the desk, usually in a
hurry, often on a device easier to lose, lend or read over the shoulder than the machine the
action will run on. It is the decision most likely to be regretted and least likely to be
revisited — nobody goes back through their standing permissions after a bus journey.

## Decision

**A phone may approve. It may not grant standing permission.** "Always allow" is never offered
at phone width, whatever `scopes_offered` said.

This is the same reasoning ADR 0008 already applied to the *action*: an ASK_ALWAYS action
offers only a one-time answer, because remembering it would defeat the point of asking. Here
it applies to the *surface*, for the same reason — the conditions under which the answer is
given determine what kind of answer it should be allowed to be.

`scopesFor` narrows and never widens: a surface cannot offer a scope the engine did not, and
`always` disappears on a phone even where the engine would have accepted it.

**The absence is explained, not merely enforced.** A control that is silently missing teaches
nothing and reads as a broken app, so the phone says why in a sentence. The owner is entitled
to know the rule.

### It is a discipline, not a boundary

This is enforced in the client, and saying so plainly matters more than the rule itself.

The core **cannot** tell which surface a request came from without trusting a header the
client sets, and a header the client sets is not a security control. Inventing one and calling
it security would be worse than this honest limit: it would look like a boundary in the threat
model and hold against nobody.

So: what this protects against is **the owner in a hurry**, which is the realistic failure. It
is not a defence against a stolen phone. For that, the answer is the one §80 already gives —
revoke the device.

## Two things the layout must not do

**Shorten the consequence.** What happens, and what happens if the owner says no, are shown in
full and wrap. A truncated consequence is the version the owner would actually read, which
makes truncating it a decision about what they are allowed to know — taken by a layout, on a
small screen, at the moment it matters most.

**Leave the machine unnamed.** At a desk the owner is at the machine. On a phone they are not,
and "delete these files" without "on Office-PC" is a different question. An unknown device is
said out loud rather than rendered as a dash, because a dash reads as "nothing" when it means
"not stated".

## Consequences

One app, told which surface it is on: the layout is chosen by viewport width rather than a
build flag, so there is no second client to keep in step and no way for the two to disagree
about what a surface may offer — `Phone.tsx` reads the rule from `lib/surface.ts` rather than
hardcoding it.

Approvals come first on the screen. They are the only thing there that is waiting on the
owner, and a phone that buries them under status has missed the point of being reachable.

**Not built, and named rather than implied:** voice remote, conversation, camera input, device
control and push notifications. Each is in §64's list, and each would need its own decision
about what a phone is allowed to do — device control especially, since "shut down the home PC"
from a phone raises exactly the question this ADR answers for approvals, and answering it by
analogy rather than deliberately is how a limit gets quietly widened.
