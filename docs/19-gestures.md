# 19. Gesture (V7)

    frames → landmarks → classify → mode gate → safety gate → command

Gesture is the only input Thursday *infers*. Typing was typed; speech was said and there is
audio to prove it. A gesture is a guess about 21 coordinates from a model looking at a hand
that may not have been addressing the computer at all. Everything below follows from that
one difference — see [ADR 0022](architecture/decisions/0022-a-gesture-is-not-a-signature.md).

## Recognition

`HandLandmarks` is 21 normalised points plus handedness and confidence — MediaPipe's
topology, but the class does not depend on MediaPipe. From those it derives finger
extension, the pinch distance (thumb tip to index tip), a pointing unit vector along the
index finger, and `aim_at()`.

`GestureTracker` keeps a short window of frames, which is what makes the motion gestures
possible: a swipe is travel over time, a drag is a pinch that moved, and zoom direction is
whether two hands are separating or closing.

| Gesture | Command | Read from |
|---|---|---|
| Point | `pointer` | index extended, others curled |
| Pinch | `select` | thumb-index distance under `PINCH_MAX_DISTANCE` |
| Pinch + move | `drag` | pinch held while the wrist travels |
| Swipe left / right | `previous` / `next` | horizontal travel inside `SWIPE_MAX_SECONDS` |
| Open palm | `stop` | five fingers extended |
| Thumbs up / down | `confirm` / `cancel` | thumb only, pointing up or down |
| Two hands apart / together | `zoom_in` / `zoom_out` | change in spread across the window |

Before V7, `classify` returned `PINCH` when *no* finger was extended — every closed or
resting hand was a click. It returns `FIST` now, and the unit test asserts that specific
case rather than the general shape, because that is the bug that was really there.

`aim_at()` projects a short distance past the fingertip: the finger points *ahead* of
itself, and this corrects for it. It is not a ray cast. Projecting further would pretend to
know the hand's distance and angle to the screen, which two-dimensional landmarks cannot
say; a long projection sends the aim off the edge of the frame as often as it improves it.

## The mode is closed by default

```
        arm()            wake word or ✌️          a command fires
OFF ───────────▶ ARMED ───────────────────▶ ACTIVE ───────────────▶ COOLDOWN
 │                 │                          ▲                        │
 │                 └──── wake word or ✌️ ─────┘   cooldown lapses       │
 └───────────────────────────────────────────┘◀──────────────────────────┘
        (OFF also opens directly on wake word or ✌️)

any watching state ──▶ OFF   on 10s idle, or "หยุดรับท่าทาง"
```

ARMED and OFF behave identically towards gestures: both discard everything except the
activation gesture or the wake word. ARMED exists so a UI can show that Thursday is
watching for the activation without yet reading commands. In neither state is a recognised
gesture dispatched — an ordinary wave is not a command because nothing is listening for
one. The mode closes itself after
`GESTURE_MODE_TIMEOUT_S`, because the failure mode of consent is a grant nobody withdrew.

`COOLDOWN_SECONDS` makes one intention one command. At 30fps a held thumbs-up is thirty
identical classifications; without the window that is thirty confirmations. The frame rate
is not a volume control.

```
GET  /api/v1/gestures        state, and the `watching` flag a UI draws its indicator from
POST /api/v1/gestures/open
POST /api/v1/gestures/close  "หยุดรับท่าทาง" — always available, never refused (§69)
```

## What a gesture may never do

**ห้าม gesture เดียวใช้ยืนยัน: delete, payment, admin, external communication, security
action.** `thursday_vision.safety.check_command` is the gate every gesture command passes,
and it refuses on any of:

| Refusal | Because |
|---|---|
| confidence below `MIN_COMMAND_CONFIDENCE` | a recogniser that is half sure is guessing |
| the action matches `NEVER_BY_GESTURE` | prefix-matched like the action policy (ADR 0007) |
| `PermissionLevel.EXTERNAL` or `RiskLevel.HIGH` and above | reached from the action's own properties, so an unlisted action is still covered |
| the action is irreversible | undo is what makes a misread survivable |

The verdict carries `needs_words`: the confirmation moves to speech or a click, it is not
re-asked as a gesture. Cancelling (`thumbs down`) is always allowed — refusing to *stop* on
the grounds of uncertainty is the one direction with no safe failure.

Risk comparisons go through `risk_at_least()`. `RiskLevel` is a `StrEnum`, so `risk >=
RiskLevel.HIGH` compares strings and ranks `LOW` above `HIGH` — silently inverting the
check it looks like it performs.

## Pointing plus speech

"Thursday เปิดอันนี้" carries no target and the gesture carries no verb; only together do
they mean anything. `VisualReferenceResolver` takes the reading and uses its aim only when
the gesture is *pointing* and at least 0.6 confident — a thumbs-up has no direction, and a
hand the tracker is guessing about must not silently outrank the mouse.

A mouse coordinate was measured, so it must land inside the element. A hand's aim was
estimated, so it gets `GESTURE_AIM_TOLERANCE` of slop and is ranked lower for it (0.72
against 0.88). Where two elements are equally near the aim, the resolution comes back
*below* the confidence floor, so the caller asks "this one?" rather than picking one and
being wrong half the time.

Past that point nothing about gestures survives. The resolved target becomes an ordinary
request and takes the ordinary path: permission, device, ACT then VERIFY.

## Not built yet

The landmark source. MediaPipe is a port dependency, not a wired one — this build
environment has no camera, and a hand-tracking pipeline that has never seen a hand is not
something to claim. `HandLandmarks` accepts 21 points from anything that produces them, and
every rule above holds regardless of which model that is. The V7 tests construct landmarks
directly, which exercises the classifier, the mode, the safety gate and the fusion, and
does not exercise MediaPipe.
