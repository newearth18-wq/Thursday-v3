# 59. A client may not assume it understands its server

Date: Sprint 86 (a bug audit)

## Status

Accepted. The consequence of [0057](0057-a-phone-is-a-screen-not-a-machine-thursday-runs-on.md)
that Sprint 84 did not follow through: once a client is a screen onto a Thursday running
somewhere else, the two are separately versioned, and every assumption the client makes about
the server's vocabulary becomes a runtime question rather than a compile-time one.

## Context

Sprint 86 was an audit rather than a feature: read the code looking for defects, in the
classes this project has repeatedly produced. Six were found. Three of them turned out to be
the same argument, which is what this decision is about.

The trigger was a two-line probe:

```
APPEARANCE[unknown] = undefined
TypeError: Cannot read properties of undefined (reading 'colour')
```

`lib/mood.ts` declares `APPEARANCE: Record<Mood, Appearance>` and its own comment explains
why: *"`Record` rather than a partial map, so adding one to the server's enum fails the build
here instead of rendering as undefined at three in the morning."* That reasoning is sound and
it protects the wrong build. It fails **this** build — the one being compiled, which
necessarily knows about the mood that was just added. The build it cannot protect is the one
already installed on somebody's phone.

And under ADR 0057 that build is the normal case, not the edge case. An Android app is a
screen onto a Thursday on the owner's PC, at an address a person typed. The app updates when
an app store says so; the PC updates when the owner runs an installer. A tenth mood on the
server therefore reaches an older client as a string it has never seen, `APPEARANCE[mood]` is
`undefined`, `.colour` throws inside render, and React unmounts the tree: white screen, no
HUD, no avatar, and no visible way to press stop.

The second finding had the same shape from the other direction. `Avatar.tsx` stops advancing
the animation clock entirely when the owner has asked for `prefers-reduced-motion`. Sprint 85
then derived the blink from that clock as `clock % BLINK_EVERY < BLINK_FOR` — which is
**true at zero**. So the frozen clock was not a still robot. It was a robot with its eyes
permanently shut, for the whole session, on exactly the machines whose owners had asked for
something gentler. Nothing failed; it just sat there, blind.

## Decision

**Two rules, and they are the same rule twice.**

**1. The client renders only what it can draw.** `knownMood` and `knownPosture` check an
incoming value against the tables that define what this build knows how to paint, and return
a value from those tables or a safe one. They are called once, in `readExpression`, where the
frame is turned into an `Expression` — a choke point, not nine call sites each hoping. This is
the same allowlist-not-filter rule `plain.py` applies to what a person is *told*, applied now
to what a person is *shown*.

The fallback is **`CONCERNED`, not `CALM`**, and the asymmetry is the whole of the judgement.
A client too old to understand its server genuinely is a part of Thursday that is not working,
so `CONCERNED` is not a euphemism — it is accurate. And of the two ways to be wrong, painting
a calm blue face over a failure the client could not read is the one ADR 0054 exists to
prevent; painting concern over an unrecognised success is merely wrong, and the sentence
printed underneath is still the server's own words, so only the colour is a guess.

**2. A frozen animation clock must render the pose at rest.** Reduced motion means less
motion, not a different robot. The blink moved to the *end* of its cycle so that `clock: 0`
draws an awake robot, and `Robot.test.tsx` asserts it — so the next thing driven from that
clock has to answer for its own zero too. `visorPulse(0)` already returned a mid-value and
needed nothing.

## Consequences

`Object.hasOwn`, never `in`. The first version of the guard used `value in APPEARANCE`, and
the test written for it failed on `"__proto__"` — because `in` walks the prototype chain, so
`"__proto__" in APPEARANCE` is `true` and the guard cheerfully returned `"__proto__"` as a
mood. Its "appearance" is `Object.prototype`, whose `.colour` is `undefined`, which reaches
`withAlpha` and paints the interface in the CSS colour `"undefinedb3"`. The guard was a
smaller version of the bug it was written to fix, and the only reason it did not ship that way
is that the test enumerated hostile keys rather than plausible ones.

`MOODS` and `POSTURES` are derived from the tables with `Object.keys`, not written out a
second time, and a test asserts they match — a hand-maintained second list is the thing that
drifts.

**The other three findings are not about clients and are recorded here only because the audit
found them together.** `InProcessEventBus._seen` was an unbounded `set` of every event id
Thursday had ever published, on the hottest path in the system, declared two lines below a
`_history` list that was bounded from the start — which is what makes it a slip rather than a
decision; it is now an insertion-ordered dict with an eviction window, and the cost (a replay
older than the window is delivered again) is exactly the at-least-once contract the class
already documents. `VoiceStateMachine._history` grew by one entry per wake, utterance and
barge-in, forever, to serve a debugger that reads the last few; it is a bounded `deque` now,
matching `BenchmarkProfile.samples`, which had the bound and the comment explaining it all
along. `VoiceService._state_events` was appended to and read by nothing anywhere in the
repository, and is gone.

And `Supervisor._llm_critique` called `self._models.complete(...)` under a
`# type: ignore[attr-defined]` while mypy was actually reporting `union-attr` — "this can be
None". The one tool that had noticed the model registry might be absent had been told to be
quiet about a different thing. It never crashed, because its single caller checks first, in
another method; the guard is local now, and returns a failed-but-recoverable check rather than
raising, because the supervisor is the one component whose entire job is to be trustworthy
about whether work succeeded.

**Two audits are now tests.** `test_structural_audit_v86.py` walks every module for ordered
comparisons on a `StrEnum` — the trap that once let a guest take CRITICAL actions, because
`"HIGH" < "LOW"`. A guard for that already existed and covered one module and one enum; there
are forty-four StrEnums here and the trap is identical in all of them. The sweep for
unbounded collections is *not* repo-wide, deliberately: it returned thirty-nine hits, mostly
test doubles, and a test needing a hand-maintained allowlist of exceptions rots into noise.
The four objects that actually matter are named instead.

mypy findings fell from 26 to 20. Of the remainder, all are the duck-typed container fields
(`Any` by construction) or Windows-only `ctypes.windll` behind a platform guard.
