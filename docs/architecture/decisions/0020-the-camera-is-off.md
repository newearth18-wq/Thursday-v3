# ADR 0020 — The camera is off, and that is provable rather than promised

**Status:** accepted · **Date:** 2026-09-02

## Context

A camera that is on when its owner believes it is off is the worst failure this system can
have. It is worse than a wrong answer and worse than a lost task, and unlike either of those
it cannot be put right by apologising afterwards.

The usual design — a setting, a permission dialog at install time, a light the application
decides when to turn on — fails for a reason that has nothing to do with intent. The
indicator is computed from one variable and the capture path reads another, and the first
time they disagree is the time it matters.

## Decision

Four properties, each chosen so the guarantee holds without anyone remembering it.

**Opening requires a reason.** `grant_access(reason)` rejects a blank one. There is no code
path that turns a camera on as a side effect of something else, and the owner reading their
own camera log later sees *why*, not a bare timestamp.

**Grants are narrow and expiring.** A default window of two minutes, and `max_captures=1`
for a single look. "Yes, look at this book" must not become "yes, watch the room". A
one-shot grant is spent the moment it is used, not when it expires.

**The indicator is derived, not maintained.** `indicator_on` returns `state is ACTIVE` —
the same field `capture()` checks. Making the light disagree with reality would require
changing one expression, not forgetting to update a second variable.

**An idle camera closes itself.** The failure mode of consent is not a refused request; it
is a granted one nobody remembered to withdraw. `CameraSweeper` runs on a timer so the
guarantee does not depend on a caller.

Two smaller rules follow from the same reasoning. `VisionService` **checks** a grant and
never creates one: a component that can grant itself camera access has no permission model,
only a habit of asking forgiveness. And when the hardware fails to close, the state reports
`OFF` anyway and logs an error — an indicator stuck on `ACTIVE` is the more alarming lie,
and a camera that cannot be closed is a fault the owner must hear about.

## Consequences

- Answering "what is this?" takes two steps: Thursday asks, the owner grants, Thursday
  looks. That friction is the feature.
- The container builds `CameraManager(None)` — a manager with no source attached. A checkout
  that has never been given a camera cannot accidentally have one opened.
- `GET /vision/camera/log` makes "when was my camera on?" answerable by the owner rather
  than by a support ticket.
- **Cost we accepted:** a grant can expire mid-task, and a long vision session needs
  renewing. Better than the alternative, which is a window wide enough that nobody notices
  it is open.

## Alternatives considered

- **A single on/off setting.** Rejected: it has no reason attached, no expiry, and no
  record. It answers "may Thursday use the camera" and never "why is it on right now".
- **Ask once, remember forever.** This is how most software does it, and it is how a camera
  ends up on for six months because of a question answered in a hurry.
- **Trust the OS indicator.** A real defence and not a sufficient one — it says the hardware
  is streaming, not who asked for it or what for, and on a headless node there is no light
  at all.
