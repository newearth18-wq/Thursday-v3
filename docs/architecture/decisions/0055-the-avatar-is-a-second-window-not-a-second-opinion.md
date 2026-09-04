# 55. The avatar is a second window, not a second opinion

Date: Sprint 82 (FULL-SCREEN HUD & AVATAR)

## Status

Accepted. Depends on [0054](0054-a-mood-is-derived-and-it-is-thursdays-own.md).

## Context

The request was for Thursday to be present while the owner is working somewhere else: a
small robot that moves about, shows how things are going, and says what it is doing. Two
questions had to be answered before any of it could be drawn. What does "somewhere else"
mean, and where does the robot's mood come from?

Both have an easy answer that is wrong.

"Somewhere else" is easiest to answer by watching: idle timers, input hooks, foreground
process names, a camera checking whether anybody is at the desk. Every one of those is
Thursday observing the owner in order to decide when to appear, and this is the same
project whose §55 forbids inferring anything about a person and whose §9 keeps biometrics
out of every context that is not the identity system.

The mood is easiest to answer locally: the avatar window has a socket, it can watch events
and decide for itself how lively to be. That is how, within a week, the HUD reads
"กำลังทำงานให้อยู่" while the robot is sitting down.

## Decision

**"Somewhere else" means Thursday's own window is not the one in front.** The only trigger
is `WindowEvent::Focused(false)` on the main window, plus the window being hidden. Thursday
learns nothing about the owner from this — not what they are using, not whether they are
there, not how long they have been away. It knows it is not the window in front, and that
is the whole of it. Opening Thursday puts the robot away again, because that is the one
unambiguous "I am here".

**The avatar is a second window onto one expression.** It renders `expression` from the
socket and computes nothing: mood, colour and the sentence in the bubble all arrive already
decided by ADR 0054. `gaitFor()` maps a mood to a way of walking and that is the extent of
this window's opinion. Disconnected, it sits down rather than strolling cheerfully about,
which is the avatar equivalent of reporting success on no evidence.

**It is a real OS window, transparent and click-through.** It has to be above whatever the
owner is actually working in, and no element inside another window can be. The window
therefore covers the primary monitor, and `set_ignore_cursor_events(true)` is not a detail:
without it Thursday becomes a sheet of glass over the desktop that swallows every click.

**Which half of the bundle to render is decided by an injected flag**, not by a hash or a
query string. Both windows ship as one build — a separate entry point would drift the first
time either was edited — and a URL fragment has to survive Tauri's own handling on three
platforms to arrive intact. A window that quietly loads the wrong half is a bug nobody sees
until the packaged build.

**The walking is a plain function.** `stride()` takes a walker, a gait and a width, and is
tested for staying on screen over thousands of frames, for turning at the edges, for coming
to rest with its feet together, and for surviving a `dt` of a hundred thousand — the frame
after a laptop lid opens. A desktop pet that has drifted off the edge is not obviously
broken; it is simply gone, and the owner concludes the feature does not work.

## Consequences

The robot can only be as expressive as Thursday's telemetry is informative — nine moods and
five gaits, and no reaction to what the owner said or did. That is the trade ADR 0054
already made, and it holds here for the same reason.

The avatar is best-effort. A machine whose compositor cannot give us a transparent
always-on-top window logs the failure and gets the whole of Thursday minus a robot; failing
to start over a decoration would be the wrong trade. On macOS transparency needs the
`macos-private-api` feature, which is named in `Cargo.toml` rather than enabled quietly,
because an App Store build would have to drop it and would then have to drop this window.

Two things were found while building it. `Container.emergency_stop` published nothing on the
bus, so a stop with no running task and no connected device reached no open window until
something unrelated happened — the loudest state in the system was the one nothing was told
about. And the desktop shell did not compile at all: the icons `tauri.conf.json` points at
were never committed, so `tauri::generate_context!` failed on a clean checkout. Nothing had
ever tried to build it, which is now a CI job.
