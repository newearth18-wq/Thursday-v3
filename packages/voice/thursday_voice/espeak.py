"""eSpeak NG — a voice that needs no model file and no network (§58, V12).

Thursday's other local synthesiser, Piper, is better sounding and needs a neural voice
downloaded per language. This one is formant synthesis: it is unmistakably a machine, and it
is **48 languages including Thai in a wheel that pip installs**, with nothing to fetch
afterwards. That trade is the whole reason it exists here — "offline mode still has a voice"
stops being conditional on somebody having downloaded the right `.onnx` first.

It is a `TTSProvider` like any other (ADR 0001), so the ranking is a setting rather than a
rewrite: `THURSDAY_TTS_BACKEND=piper` where a voice file exists, `espeak` where none does.

Three things here are load-bearing, and all three were found by measuring rather than by
reading the documentation — see ADR 0061:

**The output mode must be `AUDIO_OUTPUT_SYNCHRONOUS`, not `AUDIO_OUTPUT_RETRIEVAL`.** Both
deliver samples to a callback and never open an audio device, and the names suggest
retrieval is the one for a server. It is not: retrieval synthesises on eSpeak's own internal
thread, and `espeak_Synchronize` does not reliably fence it, so audio from one utterance
arrives after the next has cleared the buffer. Measured over fifteen identical calls:
retrieval produced seven different durations with four gross outliers — the same Thai
sentence coming back as 4.07s, 7.41s, 8.46s and 8.55s — while synchronous produced
**zero outliers**. Nothing raises; the duration is simply wrong, and a duration is what
times every subtitle downstream.

**The synthesis callback must outlive the call that registers it.** eSpeak keeps the raw
function pointer, so a `CFUNCTYPE` built inside a method is freed the moment that method
returns and eSpeak is left calling into freed memory. `_CALLBACK` is module-level and
created once. This is a ctypes requirement rather than a bug that was observed here — the
wrong durations above came from the output mode — but it is no less required for that.

**Rate below 100 wpm is not usable for Thai.** Measured: 100 wpm → 6.90s, 110 → 6.31,
120 → 5.80, a clean curve — and 90 wpm → **17.46s**, two and a half times the duration of
the rate below it. English does not do this. The floor is a clamp rather than a comment
because the caller asking for a slow voice is not the caller who can debug it.
"""

from __future__ import annotations

import asyncio
import ctypes
from collections.abc import AsyncIterator
from typing import Any

from thursday_core.logging import get_logger
from thursday_core.persona import VOICE_PROFILES
from thursday_shared.audio import to_wav
from thursday_shared.enums import VoiceMode

log = get_logger(__name__)

#: eSpeak's own default, and the middle of its usable range.
BASE_RATE_WPM = 175

#: Below this, Thai durations stop following the rate. See the module docstring.
MIN_RATE_WPM = 100
MAX_RATE_WPM = 400

#: `espeak_Initialize` output mode. Samples go to a callback and no audio device is opened,
#: which is what a server wants. SYNCHRONOUS rather than RETRIEVAL (1) because retrieval
#: synthesises on eSpeak's own thread and leaks audio between utterances — see the module
#: docstring for the measurements.
_AUDIO_OUTPUT_SYNCHRONOUS = 2

#: `espeak_SetParameter` selectors.
_RATE, _VOLUME, _PITCH = 1, 2, 3

#: `espeak_Synth` flag: the text is UTF-8.
_CHARS_UTF8 = 1

_CALLBACK_TYPE = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.POINTER(ctypes.c_short), ctypes.c_int, ctypes.c_void_p
)

#: Everything below is process-global because **eSpeak is**. One initialised engine, one
#: registered callback, one buffer, and a lock so two synthesis calls cannot interleave into
#: each other's audio. A per-instance version of any of these would be a per-instance view of
#: state that is not per-instance.
_lock = asyncio.Lock()
_library: ctypes.CDLL | None = None
_sample_rate = 0
_buffer: list[bytes] = []


def _collect(wav: Any, samples: int, events: Any) -> int:
    """eSpeak's synthesis callback. Returning non-zero would abort the utterance."""
    if wav and samples > 0:
        block = ctypes.cast(wav, ctypes.POINTER(ctypes.c_char * (samples * 2)))
        _buffer.append(bytes(block.contents))
    return 0


#: Created once, at import, and never reassigned. See the module docstring.
_CALLBACK = _CALLBACK_TYPE(_collect)


def available() -> bool:
    """Whether this machine can speak through eSpeak, asked without initialising it."""
    try:
        import espeakng_loader  # noqa: F401
    except ImportError:
        return False
    return True


def _engine() -> tuple[ctypes.CDLL, int]:
    """The initialised library and its sample rate. Initialises at most once per process."""
    global _library, _sample_rate
    if _library is not None:
        return _library, _sample_rate

    import espeakng_loader

    library = ctypes.CDLL(espeakng_loader.get_library_path())
    library.espeak_Initialize.restype = ctypes.c_int
    rate = library.espeak_Initialize(
        _AUDIO_OUTPUT_SYNCHRONOUS, 0, espeakng_loader.get_data_path().encode(), 0
    )
    if rate <= 0:
        raise RuntimeError(f"espeak_Initialize returned {rate}")
    library.espeak_SetSynthCallback(_CALLBACK)
    _library, _sample_rate = library, rate
    log.info("espeak_initialised", sample_rate=rate)
    return library, rate


class EspeakTTS:
    """Local speech, no model file, no network."""

    name = "espeak-ng"
    local = True

    def __init__(self, *, voice: str = "th", base_rate_wpm: int = BASE_RATE_WPM) -> None:
        self.voice = voice
        self.base_rate_wpm = base_rate_wpm
        self._stopped = False

    @property
    def sample_rate(self) -> int:
        return _sample_rate

    def _settings(self, mode: str) -> tuple[int, int, int]:
        """Prosody (§6) mapped onto eSpeak's three integer knobs."""
        try:
            profile = VOICE_PROFILES[VoiceMode(mode)]
        except (ValueError, KeyError):
            profile = VOICE_PROFILES[VoiceMode.NORMAL]
        rate = int(self.base_rate_wpm * float(profile["rate"]))
        rate = max(MIN_RATE_WPM, min(MAX_RATE_WPM, rate))
        # eSpeak pitch and volume are 0-100 and 0-200 around defaults of 50 and 100.
        pitch = max(0, min(99, int(50 + float(profile["pitch"]) * 50)))
        volume = max(0, min(200, int(100 * float(profile["volume"]))))
        return rate, pitch, volume

    def _synth(self, text: str, *, mode: str, voice: str | None) -> bytes:
        """The blocking half. Runs in a worker thread, under the module lock."""
        library, sample_rate = _engine()
        rate, pitch, volume = self._settings(mode)

        _buffer.clear()
        library.espeak_SetVoiceByName((voice or self.voice).encode())
        library.espeak_SetParameter(_RATE, rate, 0)
        library.espeak_SetParameter(_PITCH, pitch, 0)
        library.espeak_SetParameter(_VOLUME, volume, 0)

        encoded = text.encode("utf-8")
        library.espeak_Synth(encoded, len(encoded), 0, 0, 0, _CHARS_UTF8, None, None)
        library.espeak_Synchronize()
        pcm = b"".join(_buffer)
        _buffer.clear()
        return to_wav(pcm, sample_rate)

    async def synthesize(
        self, text: str, *, mode: str = "NORMAL", voice: str | None = None
    ) -> bytes:
        """One utterance as WAV bytes. Empty text gives an empty WAV, never an exception."""
        if not text.strip():
            return to_wav(b"", _sample_rate or 22050)
        async with _lock:
            # Synthesis is CPU work measured in hundreds of milliseconds, so it goes to a
            # thread rather than stalling the event loop. Inside the lock, because the
            # engine, the callback and the sample buffer are all process-global: two
            # synthesis calls in flight together would collect each other's audio.
            return await asyncio.to_thread(self._synth, text, mode=mode, voice=voice)

    async def stream_synthesize(
        self, text: str, *, mode: str = "NORMAL", voice: str | None = None
    ) -> AsyncIterator[bytes]:
        """A sentence at a time, so barge-in has a seam to cut on (§57)."""
        from thursday_voice.providers import _sentences

        self._stopped = False
        for sentence in _sentences(text):
            if self._stopped:
                return
            yield await self.synthesize(sentence, mode=mode, voice=voice)

    async def stop(self) -> None:
        """Stop between sentences.

        `espeak_Cancel` exists and is deliberately not called: it mutates the same global
        engine a synthesis running in a worker thread is using, and the audio for the
        current sentence has already been rendered anyway. The seam is the sentence
        boundary, which is what `stream_synthesize` yields on.
        """
        self._stopped = True
