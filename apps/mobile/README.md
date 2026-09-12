# Thursday on a phone

**There is no separate mobile codebase, and this directory holds no code.** The Android app is
`apps/desktop`, cross-compiled: `npx tauri android build` produces the `.apk`, CI builds one on
every commit, and the frontend it loads is the same React app the desktop window runs
([ADR 0057](../../docs/architecture/decisions/0057-a-phone-is-a-screen-not-a-machine-thursday-runs-on.md)
— a phone is a screen, not a machine Thursday runs on).

This file used to describe a Flutter app. It was written before that decision and never
revisited, so it named a framework, a directory layout and a wiring plan for a client nobody
was building, while the real one shipped from somewhere else. It is kept as a pointer rather
than deleted because "where is the mobile app" is a reasonable question to arrive here with.

## What the phone is for

A **remote and an approval surface**, not a second desk. State lives in the core, so a phone
picks up what a PC started (§23, §65).

| | |
|---|---|
| See what is happening | which machines are on, what is running and how far along |
| Answer what is being asked | approvals (§38) — the owner is reachable; the desk is not |

`apps/desktop/src/components/Phone.tsx` is that layout, chosen by viewport width rather than a
build flag: one app, told which surface it is on.

## The rule that is not about layout

**A phone may approve. It may not grant standing permission.**

"Approve once" answers a question the owner can see described in front of them. "Always allow"
is a durable grant of authority that outlives the moment — and the moment is the problem: a
phone approval happens away from the desk, usually in a hurry, often on a device easier to
lose or read over the shoulder than the machine the action will run on.

It is the same reasoning [ADR 0008](../../docs/architecture/decisions/) applied to the
*action* — an ASK_ALWAYS action offers only a one-time answer — applied here to the *surface*.
See `apps/desktop/src/lib/surface.ts`, and
[ADR 0069](../../docs/architecture/decisions/0069-a-phone-may-approve-but-may-not-grant-standing-permission.md)
for why it is a discipline in the client rather than a boundary the core enforces.

## Not built

Voice remote, conversation, camera input, device control and push notifications. Each is in
§64's list and none of them is here; the two above are what a phone is good for on the day the
owner is away from the desk, and the rest would each need its own decision about what a phone
is allowed to do.
