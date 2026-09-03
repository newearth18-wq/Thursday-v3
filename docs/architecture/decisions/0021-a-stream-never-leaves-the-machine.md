# ADR 0021 — A video stream never leaves this machine

**Status:** accepted · **Date:** 2026-09-02

## Context

The straightforward way to give an assistant sight is to send it the video. It is also the
one design that cannot be made acceptable by improving it.

The problem is volume, not any single frame. A camera at 30fps produces 108,000 images an
hour. A person cannot review that, cannot meaningfully consent to it, and cannot un-send it.
Consent to "Thursday can see" and consent to "everything my camera records goes to a server"
are different agreements, and only the first is one anybody actually gives.

## Decision

The stream is never the unit that travels. What travels — when anything does — is a single
frame that a **local** detector already decided was worth a second look.

`FrameSampler` applies three gates in order, each cheaper than the next:

1. **Interval.** Never more often than `min_interval_s`, whatever the source does.
2. **Change.** A frame that looks like the last one is not news.
3. **Interest.** A local detector found something above a threshold.

Plus a hard `max_per_minute` cap, which is the one that actually matters: without it, a
scene that flickers past the change threshold becomes a stream by another name, which is
precisely the thing being prevented.

The ordering inside `VisionService.read_frame` follows from this — detection is local and
runs first, and its result decides whether anything further happens. `watch()` consumes a
stream and discards frames as it goes; what comes out is a handful of readings, which is
the only form in which anything Thursday saw persists.

Events carry labels and counts, never pixels. What Thursday saw is a fact about the owner's
home; the picture of it does not go on a bus that fans out to subscribers.

## Consequences

- The change signature is a byte histogram, which is invariant to arrangement: a scene whose
  contents moved without changing the colour distribution reads as unchanged. That errs
  towards *not* capturing — a missed frame costs a second look, an extra frame costs
  privacy — and the detector gate catches what matters regardless.
- Spatial memory stores observations, never frames: label, confidence, place, time. "Where
  are my keys" is answerable without a single image being kept (§25).
- **Cost we accepted:** Thursday can miss a brief event between samples. A system that saw
  everything would be a system nobody could consent to.

## Alternatives considered

- **Stream to a cloud model, retain nothing.** Rejected: "we do not keep it" is a promise
  the owner cannot verify, and the volume problem is unchanged.
- **Downsample and stream.** Rejected: fewer frames of everything is still everything.
- **Record locally, upload on request.** Rejected — it creates the archive whose absence is
  the point, and an archive that exists will eventually be asked for.
