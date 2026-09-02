# ADR 0017 — Speech providers are a chain, and it never falls forward onto the cloud

**Status:** accepted · **Date:** 2026-09-02

## Context

Cloud speech recognition is better than local speech recognition. It is also unavailable on
a train, behind a captive portal, and whenever the vendor is having an afternoon. The
requirement — cloud may be primary, local must be able to take over — is easy to state and
easy to implement badly.

Badly means: at startup. A system that picks its provider when the process boots discovers
the network is gone at the moment the owner speaks, and by then the utterance is lost.

## Decision

`STTChain` and `TTSChain` hold an ordered list and try each in turn, **inside a single
utterance**. A provider that fails is stepped over; the owner never repeats themselves.

Two rules keep this from becoming a privacy hole:

- The chain is tried **in order**, so a local-first chain never reaches for the cloud as a
  latency optimisation. Order is the policy.
- With `local_only` set — the default, because audio is HIGHLY_PRIVATE (§34) — non-local
  providers are **skipped entirely**, not tried and rejected. A request that fails after
  the audio has left is not a refusal; it is a leak with an error message attached.

A chain with nothing eligible raises rather than degrading. Silence is a worse answer than
an error, but both are better than an upload the owner did not agree to.

Streaming gets special handling: a stream is consumed as it is read, so a provider that
dies halfway has taken the audio with it. The chain therefore buffers the utterance and
falls back on the whole thing — slower, and correct.

## Consequences

- The stub provider is always last in the shipped chain, so there is always something that
  answers. An assistant that goes mute because a model file is missing is worse than one
  that degrades to a plain voice.
- `chain.failures` records what went wrong, so "why is transcription slow today" has an
  answer that does not require reading logs.
- **Cost we accepted:** the first failure in each utterance is paid in latency, every time,
  because the chain does not remember that a provider is down. Caching that would mean
  guessing when to retry, and a stale "the cloud is down" is how a system stays degraded
  for an hour after the network came back.

## Alternatives considered

- **Choose the provider at startup.** Rejected: the failure arrives mid-utterance, which is
  precisely when startup decisions are no longer useful.
- **A circuit breaker.** Sound for high-volume services. For a personal assistant making a
  handful of requests an hour, the breaker would spend most of its life holding stale
  state.
- **Ask the owner which to use when one fails.** Rejected: they are mid-sentence.
