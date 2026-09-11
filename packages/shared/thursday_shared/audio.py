"""WAV, read and written from its own bytes.

Here rather than in `voice` or `media` because both need it and neither may import the
other. It is a container format and a little arithmetic — no codec, no library, no ffmpeg —
which is what lets the narration pipeline time subtitles against real speech on a machine
where ffmpeg is not installed at all.
"""

from __future__ import annotations

import struct

#: A RIFF header is 44 bytes. Anything shorter cannot be a WAV whatever it claims.
HEADER_BYTES = 44


def to_wav(pcm: bytes, sample_rate: int, *, channels: int = 1, bits: int = 16) -> bytes:
    """Wrap raw PCM in a RIFF header."""
    if sample_rate <= 0:
        raise ValueError(f"a sample rate of {sample_rate} is not a sample rate")
    frame = channels * (bits // 8)
    byte_rate = sample_rate * frame
    return b"".join(
        (
            b"RIFF",
            struct.pack("<I", 36 + len(pcm)),
            b"WAVEfmt ",
            struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, frame, bits),
            b"data",
            struct.pack("<I", len(pcm)),
            pcm,
        )
    )


def is_wav(data: bytes) -> bool:
    """Whether these bytes are a WAV at all.

    The narration pipeline asks this before trusting a synthesiser's output: the offline
    `TextStubTTS` returns a JSON description of how Thursday *would* have spoken, which is
    the right answer for a test asserting prosody and not something that can be laid under a
    video.
    """
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"


def wav_seconds(data: bytes) -> float | None:
    """How long a WAV is, from its own header. `None` when these are not readable as one.

    `None` rather than `0.0` on purpose, and the distinction carries: a file whose length
    cannot be read is not a file of length zero, and the caller timing subtitles against it
    must refuse rather than place a cue at the start of a silence it invented.
    """
    if not is_wav(data) or len(data) < HEADER_BYTES:
        return None

    index = 12
    sample_rate = channels = bits = 0
    while index + 8 <= len(data):
        chunk = data[index : index + 4]
        size = struct.unpack("<I", data[index + 4 : index + 8])[0]
        body = index + 8
        if chunk == b"fmt " and body + 16 <= len(data):
            _, channels, sample_rate, _, _, bits = struct.unpack("<HHIIHH", data[body : body + 16])
        elif chunk == b"data":
            frame = channels * (bits // 8)
            if not frame or not sample_rate:
                return None
            # The header's declared size, capped by what is actually there. A file truncated
            # mid-write declares the length it meant to have, and believing it would time a
            # subtitle against speech that was never written.
            actual = min(size, len(data) - body)
            return actual / frame / sample_rate
        # Chunks are word-aligned: an odd-sized one is followed by a pad byte.
        index = body + size + (size % 2)
    return None
