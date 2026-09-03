# ADR 0022 — A gesture is not a signature

**Status:** accepted · **Date:** 2026-09-02

## Context

Gesture control fails differently from every other input Thursday has.

Typing is deliberate: the keys were pressed. Speech is deliberate: the words were said, and
`VoiceService` has the audio to show for it. A gesture is *inferred* — from 21 landmark
coordinates, produced by a model, from a frame, of a hand that may not have been addressing
the computer at all. Every step of that chain has a failure mode, and the failures are not
rare edge cases: a hand resting on a desk, a wave at a colleague, someone stretching.

The pre-V7 classifier made the point concretely. It returned `PINCH` when no finger was
extended — so every closed or resting hand in view was a click. The bug is fixed, but the
class of bug is permanent. Recognition will keep being wrong sometimes, and the design has
to be correct under that assumption rather than under the assumption of a good model.

Two properties make this worse than an ordinary false positive:

- **A held gesture repeats.** At 30fps, holding a thumbs-up for a second is thirty frames
  classified identically. Naively, that is thirty confirmations.
- **A gesture leaves no record of intent.** After the fact, "the user gave a thumbs-up"
  cannot be distinguished from "the model thought so". There is no utterance to replay.

## Decision

Three rules, each addressing one of the failure modes above.

**1. Gestures are off unless the owner opened the mode.** `GestureMode` is a four-state
machine — OFF → ARMED → ACTIVE → COOLDOWN — opened only by the wake word or by an explicit
activation gesture (`peace`), and closing itself after `GESTURE_MODE_TIMEOUT_S` of no
interaction. Outside ACTIVE, a recognised gesture is discarded rather than dispatched. An
ordinary wave is not a command, because Thursday is not listening for one.

**2. A gesture may never be the confirmation for anything consequential.**
`thursday_vision.safety` refuses `may_confirm` for any action that is irreversible, above
`RiskLevel.MEDIUM`, at or beyond `PermissionLevel.EXTERNAL`, or whose name matches
`NEVER_BY_GESTURE` — deletion, payment, shell and system control, credentials and the vault,
permission and approval changes, outbound email and messages, installs. The refusal is not
"ask again with a gesture"; the verdict carries `needs_words`, and the confirmation has to
arrive as speech or a click. Cancelling (`THUMBS_DOWN`) is always permitted: refusing to
*stop* on the grounds of uncertainty is the one direction that has no safe failure.

This is the spec's rule verbatim — *ห้าม gesture เดียวใช้ยืนยัน: delete, payment, admin,
external communication, security action* — and it is enforced in one place, on the path all
gesture commands take, in the same shape as ADR 0011's rule for permissions.

**3. One intention is one command.** A `COOLDOWN_SECONDS` window after every dispatched
command means a held gesture produces one command and then, at most, a deliberate repeat
once the window lapses. The frame rate is not a volume control.

Recognition confidence gates all three: below `MIN_COMMAND_CONFIDENCE` nothing is
dispatched, and below 0.6 a pointing vector is not even allowed to steer reference
resolution, where it would otherwise silently outrank the mouse.

## Consequences

- Gestures are useful for the things they are actually good at — pointing, next/previous,
  zoom, stop, and *cancel* — and structurally cannot reach the things that would make a
  misread expensive.
- A gesture aim is an estimate, so `VisualReferenceResolver` gives it `GESTURE_AIM_TOLERANCE`
  of slop that a mouse coordinate does not get, and ranks it lower for that. Where two
  elements are equally near the aim, the resolution is returned *below* the confidence floor
  so the caller asks rather than picks.
- **Cost we accepted:** confirming a deletion needs words even when the owner's hand is
  already up and they meant it. That is a second of friction against a class of error that
  is silent, plausible and unrecoverable.
- The landmark *source* is a port. MediaPipe is not wired in this version (no camera in the
  build environment) and is not claimed to be — `HandLandmarks` is fed by whatever produces
  21 points, and every rule above holds regardless of which model that is.

## Alternatives considered

- **Confirm with a gesture, but require a second, different gesture.** Rejected: two
  inferences from the same misread frame are not independent evidence. A hand held still is
  classified the same way twice.
- **Raise the confidence threshold for consequential actions instead of forbidding them.**
  Rejected: model confidence is not calibrated to intent. A resting hand can be recognised
  as a fist with high confidence and be no kind of instruction.
- **Debounce by requiring N consecutive frames.** Kept as part of recognition (the tracker
  holds a window), rejected as a *safety* mechanism: a hand resting still trivially satisfies
  it, which inverts the intended effect.
