# 58. What Thursday is doing and how it is going are two questions

Date: Sprint 85 (the avatar, emotion and animation addendum, §1–§22)

## Status

Accepted. Extends [0054](0054-a-mood-is-derived-and-it-is-thursdays-own.md) without weakening
it: a mood is still derived and still cannot be assigned. This decision is about how many
derived things there are.

## Context

The avatar addendum's §7 lists seventeen states — IDLE, LISTENING, THINKING, WORKING,
SPEAKING, WAITING, WAITING_APPROVAL, SUCCESS, WARNING, ERROR, SLEEP, PLAYFUL, FOCUSED,
INTERRUPTED, OFFLINE, LOCKED, AUTHENTICATING — as one state machine. Sprint 80 had shipped
nine moods on one ordered priority table. The obvious reading of the requirement is
"lengthen the table".

It does not work, and the reason is not aesthetic.

`Mood` is ordered by urgency, most urgent first, so that three cheerful signals can never
average away one failure. `FAILING` therefore outranks almost everything for `FRESH`
(45 seconds) after any job fails. Put `LISTENING` on that same table and the consequence is
immediate: **a Thursday whose last job failed forty seconds ago cannot show that the
microphone is open.** §10 requires the opposite in as many words — *"Avatar must clearly
indicate microphone state."*

No ordering of one table fixes this. Put LISTENING above FAILING and a real failure is
hidden by a microphone; put it below and a live microphone is hidden by a stale failure.
The states are not competing for one slot because they are not answering one question.

### What was actually broken

Two defects, found before any of this was written.

**`Turn.listening` had no producer.** It was declared in Sprint 80, read by `express()`,
and asserted to exist by a unit test — and nothing anywhere in the repository ever set it to
`True`. `VoiceService.listening` was live the whole time, documented in
`routers/system.py` as *"the one a UI must trust: it is true exactly when the microphone is
capturing, so the recording indicator drawn from it is never wrong."* The two were never
joined. Every recording indicator drawn from an expression frame, on every platform, in
every state, was dark.

**The test that looked like it covered this did not.** `test_a_turn_says_it_is_listening_
before_it_answers` asserted frame *ordering* — that an `expression` arrives before the first
token — and said so in its own docstring. The name was a claim the body never made. This is
the fourth documented-but-untrue claim this project has had to remove, and the first where
the untrue part was a test's name rather than a docstring.

## Decision

**Two derived axes, from one call, over one snapshot.**

`express()` now returns a `Mood` *and* a `Posture`. Mood is how it is going and keeps its
nine values, its ordering and its rule. Posture is what the body is doing —
`SPEAKING`, `THINKING`, `LISTENING`, `WORKING`, `SLEEPING`, `STILL` — ordered by nearness to
the owner. Both come out of the same call so they cannot drift, and neither is a blend.

The split falls out of the addendum's own text. §11's thinking is *"head tilt, look up, hand
near chin"*; §10's listening is *"turn toward user, lean forward, reduce idle movement"*;
§14's speaking is a visor pulse. Those are descriptions of a body, not of a feeling — which
is the clue they were never moods. A Thursday that is listening while the last job failed now
draws a worried face on an attentive body, which is both facts at once and is what actually
happened.

**The microphone is not a state.** `Expression.listening` is a plain boolean, outside both
tables, copied from the voice loop and derived from nothing. Anything inside a priority table
can be outranked, and a recording indicator that something is allowed to outrank is not an
indicator — it is a light that is on except when it matters. `Posture.LISTENING` exists as
well, for the *pose*, and it genuinely can be outranked: during barge-in the microphone is
open while Thursday is still speaking, and the body should show the speaking. The boolean
stays true through that.

The client honours the same rule three ways. `MIC` is defined in `lib/avatar.ts` rather than
in `lib/mood.ts`, because every colour in the mood palette is one a mood can change and a
mood that can change it can hide it. The indicator is drawn outside the head group, outside
every posture branch, in both windows. `Robot.test.tsx` walks all fifty-four
mood × posture combinations rather than a representative few, because the failure mode is
never "the light is wrong" — it is "the light is right until something urgent happens".

**One reader for the microphone, on the container.** `Container.microphone_open()` is what
both the socket and `GET /expression` call. The endpoint's own docstring already promised
*"one place that decides what Thursday is feeling and no way for the two to disagree"* — and
that promise would have become false the moment §10's field existed, because the endpoint
passes a world snapshot and the world snapshot has never known about the voice loop. Same
machine, same second, different answer. It lives on the container rather than in
`expression.py` because that module imports nothing that carries an observation of a person
and a test asserts its import list.

**Sleep is a derived quiet, not a timer.** `WorldStateSnapshot.last_event_at` is stamped by a
single `bus.subscribe("*")` in the projector — one choke point, not five handlers that each
have to remember, because the next handler added would otherwise be the one that lets
Thursday fall asleep mid-task. Nothing on the bus is periodic, so an idle Thursday genuinely
goes quiet. `None` — no event ever seen — counts as awake: the two mistakes are not
symmetric, and a robot that looks asleep on a machine that is working is a lie about what the
owner's computer is doing.

## What was refused

**§19's `AUTHENTICATING` is not built.** `IdentityGate` and `AuthenticationSession` exist in
`thursday_security`, but nothing constructs them on the container — there is no live
"verification in flight" signal anywhere in a running Thursday. A posture whose only
reachable value is "never" is not a state, it is a claim, and this project has now removed
four of those. `test_authenticating_is_not_a_posture_until_something_can_produce_one` asserts
its absence, so the face cannot be added without the signal, and whoever wires the identity
layer is told the avatar is waiting for it.

**§21's nine emotion variables are not nine fields.** valence, energy, confidence, attention,
urgency, task_success, task_failure and system_health are already on `Expression` under other
names — as the mood itself, as `intensity`, as `verified` and `confidence` on the turn, as
`running`/`waiting`/`unhealthy`. Adding a second, parallel representation that nothing renders
would be the same defect as `Turn.listening`, deliberately. The ninth, **`user_presence`, must
not exist at all**: §55 forbids inferring anything from a person, ADR 0055 fixed "elsewhere"
as window focus and nothing else, and the addendum's own precedence clause puts privacy above
the rest of it. The one honest presence fact — whether Thursday's window has focus — is known
only to the client and stays there.

**`PLAYFUL` and `FOCUSED` are not postures.** FOCUSED is `WORKING` at high intensity, which
`intensity` already carries. PLAYFUL is a client decision under ADR 0055's rule and belongs
in the avatar window, not on the wire. `INTERRUPTED` is a transient, and a state you can be
stuck in is the wrong shape for something that lasts 200ms.

## Consequences

`gaitFor` takes a posture, and the two-line rule is the whole integration: a posture may say
*whether* the body travels, and the mood still decides how briskly it does when it is not
engaged with the owner. `Robot.tsx` takes `posture`, `listening` and a `clock` — the last
because §8's blink and §14's visor pulse both happen while the robot stands perfectly still,
and `phase` deliberately stops with the legs, so anything drawn from it would be a
photograph.

`useRealtime`'s whole-object `as Expression` cast is gone. It satisfied the compiler whatever
fields were present, so `posture` and `listening` could have been added to the contract and
silently never read — the same hole in a different language. A typed literal means the next
field added is a build error until somebody decides what it reads.

**Two of this sprint's own tests were wrong first, in the two ways this project keeps
finding.** A check for §14's "no mouth" scanned the markup for `/mouth|lip|teeth/` and failed
against a drawing that has no mouth, because `ellipse` contains "lip" — a text scan matching a
word inside an unrelated token. It was replaced by a structural claim: when Thursday speaks,
exactly one element in the drawing changes, and it sits on the visor. That version was wrong
too, counting four changes for one moving rect because `outerHTML` contains the children's;
an ancestor is not a second thing that moved.

Every claim here was checked by breaking it. Reverting the gateway fix fails two integration
tests; carrying the flag on `feed.turn` instead — the tempting fix, and the one that looks
right — still fails the third, because the first turn drops it. Giving a mood a vote on the
microphone fails three unit tests and two component tests. Adding a posture with no producer
fails two.

**No microphone has ever been open.** No audio device exists in the environment this was
built in, so `VoiceService.listening` has only ever been true because a test made it so. What
is proved is that the value travels — from the voice loop, through the container, through
`express()`, out of both the socket and the endpoint identically, to a red light in two
windows that nothing can switch off. Whether a real microphone sets it remains V4's
unverified layer, and this changes nothing about that.
