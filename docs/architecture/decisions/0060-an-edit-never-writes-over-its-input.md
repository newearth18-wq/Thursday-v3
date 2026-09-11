# 60. An edit never writes over its input, and a render is judged by the file it produced

Date: Sprint 91

## Status

Accepted. Builds the media editing that `docs/23-release-readiness.md` listed as
"Designed, ported, not yet implemented" and that `packages/agents/thursday_agents/media.py`
opened by saying it could not do.

## Context

The brief this project answers opens with one request: *"make a short promotional video for
tomorrow's school event using the information in my project folder."* Everything around it
was built first — planning, permissions, verification, memory, the device layer — and the
media agent could read a file header and nothing else. Its own docstring said so in its first
line, which was the honest thing to do and not a thing to leave indefinitely.

So the gap was never "Thursday cannot make videos". It was that **the one capability the
brief leads with had no implementation**, and the parts of the system that would have to
carry it — permission, verification, undo, checkpointing — had never been asked to carry
something that takes four minutes and writes eight files.

Three properties of media editing make it different from everything else Thursday does, and
each one drove a decision below.

It is **slow**. A render is minutes, not the sub-second tool calls the task machinery was
built around. A crash partway through is expensive in a way a failed `file.read` is not.

It is **deterministic**. Same inputs, same command, same output, every time. That is unusual
here — models are not, devices are not, the network is not — and it is what makes resuming
safe at all.

And its failures are **silent**. ffmpeg exits zero having written a video with no subtitles
in it, because libass could not find the font. It exits zero having written a container
header and no frames. It exits zero having produced a 1920×1080 file when the owner asked
for something to put on TikTok. None of these look like failures from the outside, and all of
them are failures.

## Decision

**An edit never writes over its input.** Every operation takes a destination and produces a
new file; `_check_destination` refuses when the destination is also a source, and
`EditPlan.validate` refuses earlier, where the error is cheaper. This is the single decision
the rest of the design leans on:

- *Undo is free and correct.* Reversing an edit means deleting files this call created. No
  original is at risk, so `undo_media_edit` can delete without checking anything — the
  invariant already guarantees what it would be checking for.
- *The risk level is honest.* `media.edit` is `MODIFY`/`LOW`/`AUTO` rather than sitting
  beside `file.delete`. The worst case of getting a render wrong is wasted disk, not
  somebody's footage. A policy that asked before every render would produce approval
  fatigue for an operation that cannot destroy anything.
- *A resume cannot corrupt a source.* A re-run step overwrites its own output and reads
  inputs that no step has written over.

The cost is disk. A seven-step plan leaves seven intermediate files. That is the trade, it is
paid knowingly, and the working directory is under `data_dir` rather than beside the owner's
originals.

**A render is reported from the file, never from the exit code.** Every operation ends in
`_finish`, which checks the output exists and holds more than a container header. Every plan
ends in `quality.check_output`, which opens the deliverable and checks it against what was
*asked for* — dimensions against the requested preset, audio against whether narration was
part of the plan, cue times against the real duration. `media.edit` reports that verdict as
`verified`, which is what the Supervisor reads. A render that finished and produced the wrong
thing comes back `ok=True, verified=False`: work happened, and it is not what was asked for.
Those are different facts and collapsing them is how an assistant ends up claiming a video
it did not make.

**Expectations come from the request, not from the output.** `creative.expectations` derives
them from the `PromoRequest` before anything renders. Asking the finished file what shape it
is and then checking it is that shape proves nothing, and it is the easiest mistake to make
here because the probe is right there.

**Three states in the quality gate, not two.** `pass`, `fail`, and `unknown`. A probe that
could not read the file says nothing about whether the render was good; `QualityReport.ok` is
false for an unknown and `certain` says which kind of false it is. This is the same position
ADR 0039 takes on an interrupted step and ADR 0012 takes on verification generally: not
knowing is its own answer.

**A step resumes only when the file it produced is still exactly the size that was
recorded.** A checkpoint that skips too much is worse than no checkpoint: it hands the next
step a half-written file and the video finishes successfully with a scene missing. Missing,
shorter, longer, no entry, a different operation under the same step id, an unreadable
checkpoint — all of them re-run. The brief's own example is this case exactly (*✓ script ✓
storyboard ✓ voice ✗ rendering*), and "when safe" is the entire design problem in it.

**Dispatch is a table, never `getattr`.** A plan is data; data can come from a model; data
that reaches `getattr(editor, name)` on a live object can name any attribute on it. The
operation set is a `frozenset` checked before the first step runs.

**No shell, anywhere.** Every command is an argument list. Filenames and subtitle text reach
this package from files, from models and from the owner's speech, and a shell would make a
filename containing `;` into a command. The subtitle path gets a second layer: ffmpeg's
filter syntax gives `:`, `'` and `\` their own meanings, so an escaping bug there is a
filter-injection bug. Rather than escape, the file is copied to a temporary directory as
`subs.srt` and ffmpeg is run from there — the question is removed instead of answered.

**Missing ffmpeg is a state, not an exception.** `UnavailableEditor` satisfies the whole port
and refuses every operation with one sentence naming the remedy. The container builds it when
discovery finds nothing, `health()` reports it, and the tools are registered either way —
because "Thursday cannot do that here, install ffmpeg" is more use to the owner than
`ToolNotFound`. This is the brief's hardest rule in one class: *never show fake progress and
then report a completion that did not happen.*

**Estimated timings are never promoted to measured ones.** A subtitle track carries where its
numbers came from. Narration supplied per scene gives cues that sit on the frame the voice
starts; one file for the whole script gives cues placed by reading speed. Both are legitimate
output. They are not the same claim, `SubtitleTrack.timing` records which one is in hand, and
the quality gate repeats it rather than certifying a guess as synchronised.

## Consequences

The brief's headline scenario runs end to end on a machine with no network and no model
credentials: `tests/e2e/test_v11_media_acceptance.py` assembles a script, three pictures and
three narration files into a vertical MP4 with burnt-in Thai and English subtitles, then
opens the result and checks its dimensions, duration, audio and cue timings. Nothing in that
test asserts on a return value.

**What this does not do.** Research, scriptwriting, image generation and narration are model
work, and `compose` takes all four as inputs. A request with no pictures comes back
`ready=False` with `missing` naming what it needs, rather than rendering something and
calling it a storyboard. The brief's full creative workflow is therefore half-built on
purpose: the deterministic half, which is the half that can be trusted without a model.

**Silence removal is refused on video.** Cutting silent stretches out of a soundtrack while
leaving the picture alone puts the two out of step for the rest of the video. Doing it
properly means cutting the picture at the same points, which is a different and much larger
operation. So it works on audio — tightening a narration before it is dubbed on, which is the
case it is actually wanted for — and refuses on video with the reason. Half of a feature,
said out loud, beats a whole one that desynchronises somebody's video.

**Two bugs this work found in itself.** Both were caught by the quality gate rather than by a
person, which is the argument for having built it. Scene lengths and cue lengths were being
computed independently, so three Thai lines over a four-second video produced nearly eight
seconds of subtitles — a track running past its own picture. And a scene with a picture but
no words did not advance the subtitle clock, so every cue after a title card ran early. A cue
is now laid against the scene it belongs to, always.

**A third, in the capability probe.** `ffmpeg -version`, `-encoders` and `-filters` write to
*stdout*, unlike every other invocation in the adapter, whose output is the stderr log. The
first version read stderr for all of them and reported a fully capable build as having no
encoders, no subtitle support and no filters at all. It would have shown the owner a
capability list that was wrong in the safe direction, which is the direction that gets
believed.
