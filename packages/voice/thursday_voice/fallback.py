"""Provider fallback (V4).

Cloud speech is better. Cloud speech is also unavailable on a train, behind a captive
portal, and whenever the vendor is having an afternoon. The spec's requirement is short —
*cloud may be primary, but the system must fall back* — and the interesting part is what
"fall back" means in a voice loop.

It means: within one utterance, without asking, and without losing the turn. The owner
already spoke; making them repeat themselves because a request timed out is exactly the
failure the fallback exists to prevent.

Two rules keep this from becoming a privacy hole:

* the chain is tried **in order**, so a local-first chain never reaches for the cloud as an
  optimisation; and
* a chain carrying private audio refuses to fall *forward* onto a non-local provider — an
  offline degradation must not become an upload (§34).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from thursday_core.logging import get_logger

from thursday_voice.ports import AudioChunk, Transcript

log = get_logger(__name__)


class ProviderChain:
    """Try each provider in order; the first that answers wins."""

    def __init__(self, providers: list[Any], *, local_only: bool = False) -> None:
        if not providers:
            raise ValueError("a provider chain needs at least one provider")
        self._providers = providers
        #: When the payload is private, non-local providers are skipped entirely rather
        #: than tried and rejected — a request that fails after the audio has left is not
        #: a refusal, it is a leak with an error message.
        self.local_only = local_only
        self.failures: list[tuple[str, str]] = []

    @property
    def providers(self) -> list[Any]:
        return list(self._providers)

    @property
    def name(self) -> str:
        return "+".join(getattr(p, "name", type(p).__name__) for p in self.eligible())

    @property
    def local(self) -> bool:
        return all(getattr(p, "local", False) for p in self.eligible())

    def eligible(self) -> list[Any]:
        if not self.local_only:
            return list(self._providers)
        return [p for p in self._providers if getattr(p, "local", False)]

    async def _attempt(self, method: str, *args: Any, **kwargs: Any) -> Any:
        candidates = self.eligible()
        if not candidates:
            raise RuntimeError("no local provider is available and the payload cannot leave")

        last: Exception | None = None
        for provider in candidates:
            fn = getattr(provider, method, None)
            if fn is None:
                continue
            try:
                return await fn(*args, **kwargs)
            except Exception as exc:
                name = getattr(provider, "name", type(provider).__name__)
                self.failures.append((name, str(exc)))
                log.warning("voice_provider_failed", provider=name, method=method, error=str(exc))
                last = exc
        raise last or RuntimeError(f"no provider could handle {method}")


class STTChain(ProviderChain):
    async def transcribe(self, audio: bytes, *, language: str | None = None) -> str:
        return await self._attempt("transcribe", audio, language=language)

    async def stream_transcribe(
        self, chunks: AsyncIterator[AudioChunk], *, language: str | None = None
    ) -> AsyncIterator[Transcript]:
        """Streaming has no second chance.

        A stream is consumed as it is read, so a provider that dies halfway has taken the
        audio with it and there is nothing left to hand the next one. Rather than pretend
        otherwise, the chain buffers the utterance and falls back on the *whole* thing —
        slower, and correct.
        """
        buffered: list[AudioChunk] = []
        async for chunk in chunks:
            buffered.append(chunk)

        async def replay() -> AsyncIterator[AudioChunk]:
            for chunk in buffered:
                yield chunk

        for provider in self.eligible():
            fn = getattr(provider, "stream_transcribe", None)
            if fn is None:
                continue
            try:
                results = [t async for t in fn(replay(), language=language)]
            except Exception as exc:
                name = getattr(provider, "name", type(provider).__name__)
                self.failures.append((name, str(exc)))
                log.warning("voice_provider_failed", provider=name, method="stream", error=str(exc))
                continue
            for transcript in results:
                yield transcript
            return

        # Nothing streamed; fall back to one-shot transcription over the buffered audio.
        pcm = b"".join(c.pcm for c in buffered)
        yield Transcript(text=await self.transcribe(pcm, language=language), final=True)


class TTSChain(ProviderChain):
    async def synthesize(
        self, text: str, *, mode: str = "NORMAL", voice: str | None = None
    ) -> bytes:
        return await self._attempt("synthesize", text, mode=mode, voice=voice)

    async def stream_synthesize(
        self, text: str, *, mode: str = "NORMAL", voice: str | None = None
    ) -> AsyncIterator[bytes]:
        for provider in self.eligible():
            fn = getattr(provider, "stream_synthesize", None)
            if fn is None:
                continue
            try:
                async for piece in fn(text, mode=mode, voice=voice):
                    yield piece
                return
            except Exception as exc:
                name = getattr(provider, "name", type(provider).__name__)
                self.failures.append((name, str(exc)))
                log.warning("voice_provider_failed", provider=name, method="stream", error=str(exc))
        yield await self.synthesize(text, mode=mode, voice=voice)

    async def stop(self) -> None:
        for provider in self._providers:
            stop = getattr(provider, "stop", None)
            if stop is None:
                continue
            try:
                await stop()
            except Exception as exc:
                log.debug(
                    "tts_stop_failed", provider=getattr(provider, "name", "?"), error=str(exc)
                )
