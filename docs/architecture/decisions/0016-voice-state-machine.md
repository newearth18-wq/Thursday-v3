# ADR 0016 — The voice loop is a state machine, and barge-in is not optional

**Status:** accepted · **Date:** 2026-09-02

## Context

The first voice implementation had a boolean `awake` and an `interrupt()` method. The
method could never work: the field holding the synthesis task was assigned `None` and never
anything else, so the cancel was unreachable and every call returned "nothing to stop". The
module docstring called barge-in "a first-class state". It was not implemented at all.

That is not a coincidence of that particular code. A voice loop's hard problems are all
state problems — two overlapping utterances, a wake word firing while Thursday is already
speaking, a transcription landing after the owner has moved on — and a boolean cannot
express which of those is happening, so the code that ought to handle them has nowhere to
live.

## Decision

An explicit `VoiceStateMachine` with a declared transition table:

```
IDLE → LISTENING → CAPTURING → TRANSCRIBING → THINKING → SPEAKING → IDLE
```

Three properties are enforced by the table rather than by convention:

- **Nothing is captured before the wake word.** `LISTENING` is the only state reachable
  from `IDLE` that leads to capture, and only the wake word reaches it (T9). `IDLE →
  SPEAKING` is also legal — a proactive notification opens the speaker, never the
  microphone — which is why the guarantee is stated in terms of `LISTENING_STATES` and not
  in terms of "IDLE has one exit".
- **The recording indicator cannot lie.** `machine.listening` is derived from the state, so
  the light the owner sees is the same fact the loop acts on.
- **A stop can never be refused.** `reset()` is the one unconditional transition, because
  it is the emergency path (§69). A stop the state machine could reject is not a stop.

Barge-in is a controller that *owns* the synthesis task. It cancels it, tells the provider
to stop, and records how much the owner actually heard — which differs from what was queued
by however much audio was buffered, and that difference is what makes "as I was saying"
either right or wrong.

## Consequences

- Synthesis must be interruptible, so providers stream by sentence rather than returning
  one finished blob. `PiperTTS.stop()` kills the renderer: a synthesiser that finishes the
  sentence after being told to stop is not something the owner can interrupt.
- Every illegal transition raises instead of being logged. A voice loop that quietly ends
  up in a state nobody designed is a voice loop that records when it should not.
- **Cost we accepted:** more ceremony than a boolean, and a transition table to keep in
  step with the loop. In exchange, "is the microphone open?" has exactly one answer.

## Alternatives considered

- **Flags on the service.** What was there. It could not represent barge-in, which is why
  barge-in did not work.
- **Let the provider own interruption.** Rejected: every provider would implement it
  differently and some not at all, and the guarantee would then be the weakest of them.
- **Interrupt by discarding output.** Rejected — with a non-streaming provider the owner
  has already heard the whole utterance by the time it is discarded.
