# 61. A voice that needs no download, and a duration measured rather than guessed

Date: Sprint 92

## Status

Accepted. Closes the gap ADR 0060 left open: V11 could assemble a video around narration
somebody supplied and could not produce any, so every video Thursday made took the
estimated-timing path.

## Context

Subtitle timing has always had two grades here, and `SubtitleTrack.timing` has always
recorded which one is in hand. *Measured* means each cue sits on the frame the voice starts,
because the scene is exactly as long as the audio spoken over it. *Estimated* means the cue
was placed by reading speed — a defensible guess that drifts against real speech.

Until now only one of those was reachable. Thursday had a `TTSProvider` port with two
adapters: `TextStubTTS`, which returns the prosody envelope as JSON so a test can assert
*how* Thursday would have spoken, and `PiperTTS`, which shells out to a binary and a
per-language neural voice file. The stub cannot be laid under a video. Piper needs a
`.onnx` fetched from a model host. So the honest state of the system was that Thursday
could describe speech and could not make any, and `docs/23` said so.

That is a strange gap to leave, because the *hard* parts were all built: the port, the
fallback chain, the prosody profiles, the privacy rule that audio must not leave the
machine. What was missing was one adapter that produces samples.

## Decision

**eSpeak NG, as a library in a wheel.** `espeakng-loader` ships `libespeak-ng.so` and its
data — 114 dictionaries including Thai — installed by pip with nothing to download
afterwards. It is formant synthesis and it sounds like a machine; Piper sounds far better
and needs a voice file per language. So eSpeak goes **behind** Piper in the chain rather
than instead of it: best available first, and a floor under it that is always there. That
turns "offline mode still has a voice" from conditional on somebody having fetched the right
file into a property of a fresh install.

**Narration is a separate concern from speaking.** `thursday_media.narration.Narrator` takes
a `TTSProvider`-shaped object, speaks each line into its own WAV, and reads back how long
each one actually is. It lives in `media` rather than `voice` because the consumer is the
video pipeline, and it imports nothing from `voice` — the port is structural, so any object
with `synthesize` satisfies it.

**The durations come from the file, and the file's own header.** `thursday_shared.audio`
reads a WAV's length out of its RIFF chunks: no codec, no library, no ffmpeg. Narration
therefore works on a machine where media editing does not, and `compose` needs no editor at
all on the spoken path.

**A backend that describes speech is refused by name, not degraded around.** Asked to
narrate, a `Narrator` holding `TextStubTTS` gets 169 bytes of JSON back. It raises, naming
the backend and the setting that fixes it, before writing anything. The alternative is a
folder of files with a `.wav` extension containing `{"text": ...}` and a finished video with
silence where the voice should be — a failure with nothing anywhere to notice it.

**Speaking is all-or-none, like a supplied track.** A scene with no line cannot be narrated,
and timing some scenes by voice and the rest by reading speed would move every cue after the
first silent one. Checked before a single line is synthesised, so a refusal never leaves a
folder of audio nobody asked for.

**`narrate=True` never falls back to guessing.** A request for a spoken video answered with
a silent one is not a degraded result, it is a different result. Without a voice, `compose`
returns `ready=False` naming the remedy.

## Consequences

The brief's creative workflow now runs end to end with no supplied assets beyond pictures: a
script goes in, Thursday speaks it, each scene becomes as long as its line, and the cues are
measured. `tests/e2e/test_v12_narration_acceptance.py` renders one and then pulls the
soundtrack back out of the finished MP4 to check it peaks above silence — because every
duration assertion in that file would pass against a video with a silent track.

**Three defects found by measuring, one of which was mine twice over.**

*The output mode.* eSpeak's `AUDIO_OUTPUT_RETRIEVAL` and `AUDIO_OUTPUT_SYNCHRONOUS` both
deliver samples to a callback and open no audio device, and the name of the first suggests
it is the one for a server. It is not: retrieval synthesises on eSpeak's own internal
thread and `espeak_Synchronize` does not reliably fence it, so audio from one utterance
arrives after the next call has cleared the buffer. Over fifteen identical calls, retrieval
produced seven different durations with four gross outliers — the same Thai sentence coming
back as 4.07s, 7.41s, 8.46s and 8.55s. Nothing raises. The duration is simply wrong, and the
duration is what times every subtitle downstream.

Worse than the defect was the diagnosis. The first explanation written into the module
docstring blamed the ctypes callback being garbage-collected, which is a real hazard and was
not what was happening — it survived a second theory about thread affinity before the A/B
that actually discriminated. Keeping the callback alive is still required; attributing the
observed bug to it would have left a wrong explanation in the file and the real cause
in place.

*A claim the code did not honour.* `compose`'s docstring said the narrator's measured
durations "are used directly, so no editor is needed for that path at all". They were not:
the lengths fell through to `_scene_lengths`, which re-derived them from reading speed when
no editor was passed, so a spoken script was timed no better than a guessed one. The
docstring was written before the code and was believed instead of checked — the same
documented-but-untrue shape this project has now removed six times. The test that found it
compared the narrator's numbers against the plan's.

*Thai below 100 wpm.* Measured: 100 wpm → 6.90s, 110 → 6.31, 120 → 5.80, a clean curve —
and 90 wpm → 17.46s. English does not do it. Clamped, because the caller asking for a slow
voice is not the caller who can debug it.

**What this does not do.** eSpeak sounds like a machine, and for a video somebody will show
at a school that matters. The fix is a Piper voice file, which is a download rather than a
design change — the chain already prefers Piper where one exists. No microphone or speaker
has been involved at any point: this writes files. And `espeak_Cancel` is deliberately never
called, so barge-in cuts at a sentence boundary rather than mid-word — cancelling would
mutate the same global engine a worker thread is using.
