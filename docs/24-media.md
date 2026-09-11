# 24. Media editing (V11)

    script + pictures + narration → plan → render → probe the output → report

The brief this project answers opens with *"make a short promotional video for tomorrow's
school event."* This is the half of that Thursday can do with no network, no model
credentials and no cloud account: take material that exists and turn it into a finished
video, deterministically, on the owner's own machine.

It is deliberately half. Research, scriptwriting, image generation and narration are model
work that arrives here as **inputs**. Nothing in this layer invents a picture it was not
given.

## An edit never writes over its input

The one decision the rest of the design rests on
([ADR 0060](architecture/decisions/0060-an-edit-never-writes-over-its-input.md)). Every
operation takes a destination and produces a new file; `_check_destination` refuses when the
destination is also a source, and `EditPlan.validate` refuses earlier, where the error is
cheaper.

Three things follow, none of which needed building separately:

| Consequence | Why it falls out |
|---|---|
| **Undo is correct without checking anything** | Everything the call names, the call created. `undo_media_edit` deletes and no original is at risk |
| **The risk level is honest** | `media.edit` is `MODIFY`/`LOW`/`AUTO`. The worst case of a bad render is wasted disk, not somebody's footage |
| **A resume cannot corrupt a source** | A re-run step overwrites its own output and reads inputs no step has written over |

The cost is disk: a seven-step plan leaves seven intermediate files, under `data_dir` rather
than beside the owner's originals.

## A render is judged by the file it produced

ffmpeg exits zero having written a video with no subtitles in it, because libass could not
find the font. It exits zero having written a container header and no frames. It exits zero
having produced a 1920×1080 file when the owner asked for something to put on TikTok. None of
these look like failures from the outside.

So every operation ends in `_finish` — the output exists and holds more than a header — and
every plan ends in `quality.check_output`, which opens the deliverable and checks it against
what was **asked for**:

```
not_corrupt   size, streams present            fail stops everything else
has_video     a video stream at all
dimensions    against the requested preset     not against the output's own shape
has_audio     when narration or music was planned
duration      against the plan's own arithmetic
subtitles     no cue past the end of the picture
```

`media.edit` reports that verdict as `verified`. A render that finished and produced the
wrong thing comes back `ok=True, verified=False` — work happened, and it is not what was
asked for. Those are different facts, and the Supervisor reads the second one.

**Three states, not two.** `pass`, `fail`, `unknown`. A probe that could not read the file
says nothing about whether the render was good, so `ok` is false for an unknown and `certain`
says which kind of false it is — the same position [ADR 0039](architecture/decisions/0039-an-interrupted-step-is-unknown-not-failed.md)
takes on an interrupted step.

## Plans, and resuming one

A render is slow and perfectly deterministic. Slow means a crash three minutes in is
expensive; deterministic means the work already done is still good afterwards. That is what
checkpoints are for, and the brief names this case exactly: *✓ script ✓ storyboard ✓ voice
✗ rendering.*

A step is skipped on resume **only** when the checkpoint says it finished and the file it
produced is still on disk at exactly the size that was recorded. Missing, shorter, longer, no
entry, a different operation under the same step id, an unreadable checkpoint — all re-run. A
half-written MP4 from a killed process is a different size than the one recorded, so it is
re-rendered rather than handed to the next step, which is the failure the check exists for.

Plans are plain data. They serialise to JSON, so the same plan can be written by the planner,
stored against a task, inspected by the owner before it runs, and resumed by a different
process than started it. Dispatch is an explicit table rather than `getattr(editor, step.op)`
— a plan is data, data can come from a model, and data that reaches `getattr` on a live
object can name anything on it.

## Where subtitle timings come from

```
narration per scene  → each scene is as long as the line spoken over it → measured
one narration file   → scene lengths estimated from reading speed       → estimated
```

Both are legitimate output. They are not the same claim. `SubtitleTrack.timing` records which
one is in hand, nothing promotes an estimate into a measurement, and the quality gate repeats
the distinction rather than certifying a guess as synchronised.

A cue is always laid against **the scene it belongs to** — it appears when the picture does
and leaves when the picture does. Timing the two independently is how a caption ends up still
on screen two scenes later, and it is a bug this layer shipped with for about an hour before
its own quality gate caught it.

Thai is wrapped on character count because there is nothing else to wrap on — no spaces
between words — which breaks mid-word roughly as often as not. That is worse than a
dictionary-based line breaker and better than a line running off the side of the frame, and
it is the honest trade: a real Thai line breaker needs a dictionary this project does not
ship.

## No shell, and no escaping either

Every command is an argument list executed with `create_subprocess_exec`. Filenames and
subtitle text reach this package from files, from models and from the owner's speech, and a
shell would make a filename containing `;` into a command.

The subtitle path gets a second layer. ffmpeg's filter syntax gives `:`, `'` and `\` their
own meanings, so a real path — `C:\Users\…`, or anything with an apostrophe — has to be
escaped into that syntax, and an escaping bug there is a filter-injection bug. Rather than
escape, the file is copied to a temporary directory as `subs.srt` and ffmpeg is run from
there. The question is removed instead of answered.

Paths are also jailed: the container passes the render workspace, `data_dir` and the vault as
`allowed_roots`, and anything outside them is refused. Same reasoning as the device node's
jail (§9) — a plan can name a file.

## When ffmpeg is not there

A supported deployment, not a degraded one. Discovery runs once at startup — an explicit
setting, then the PATH, then an installed `imageio-ffmpeg` wheel — and when it finds nothing
the container builds an `UnavailableEditor` that satisfies the whole port and refuses every
operation with one sentence naming the remedy:

```
Media editing is not available on this machine: ffmpeg is not installed. Install ffmpeg
(or `pip install imageio-ffmpeg`) and restart Thursday, or set THURSDAY_FFMPEG_PATH to an
existing ffmpeg. Nothing was changed.
```

The tools are registered either way, unlike the browser tools, which disappear when
Playwright is absent. The difference is deliberate: "Thursday cannot do that here, install
ffmpeg" is more use to the owner than `ToolNotFound`. `health()` reports it, and
`MediaAgent.describe_capabilities()` answers before a plan is built rather than during one.

Capability goes further than present-or-absent. Builds differ, so `probe_capabilities` asks
the binary what it has — libx264, aac, subtitles, loudnorm, silenceremove, xfade, overlay —
because finding out that a build cannot produce H.264 at the end of a render is finding out
at the worst possible moment.

## What is deliberately not built

**Generation.** No text-to-image, no text-to-video, no TTS. `creative.compose` returns
`ready=False` with `missing` naming what it lacks, rather than rendering something and
calling it a storyboard.

**Silence removal on video.** Cutting silence out of a soundtrack while leaving the picture
alone desynchronises the two for the rest of the video; doing it properly means cutting the
picture at the same points, which is a different and much larger operation. So it works on
audio — tightening a narration before it is dubbed on, the case it is actually wanted for —
and refuses on video with the reason. Half a feature, said out loud.

**Fonts.** libass renders what fontconfig can find. A machine with no Thai font burns in
boxes, the quality gate cannot see it, and
[§23](23-release-readiness.md) says so rather than letting a green suite imply otherwise.

## Where it lives

```
packages/media/thursday_media/
  ports.py        MediaEditor protocol, MediaProbe, ExportPreset, the errors
  ffmpeg.py       the real adapter — discovery, probe, twelve operations
  unavailable.py  the refusal, with a remedy
  plan.py         EditPlan, EditStep, checkpoints, render()
  subtitles.py    cue timing, SRT/VTT, wrapping — no ffmpeg, no model
  quality.py      the gate: pass / fail / unknown
  creative.py     script + pictures → a plan, or a list of what is missing
  tools.py        media.probe and media.edit, and the undo
```

Tests: `tests/unit/test_media_*_v11.py` (no ffmpeg needed),
`tests/integration/test_media_editing_v11.py` (every operation against a real binary),
`tests/e2e/test_v11_media_acceptance.py` (the brief's headline scenario, rendered).
