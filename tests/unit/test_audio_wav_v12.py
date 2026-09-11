"""Reading and writing WAV from its own bytes (V12).

Small and worth testing precisely, because a subtitle track is timed off these numbers. An
error of a tenth of a second here is a caption a tenth of a second out for the rest of the
video.
"""

from __future__ import annotations

import struct

import pytest
from thursday_shared.audio import is_wav, to_wav, wav_seconds

RATE = 22_050


def _pcm(seconds: float, rate: int = RATE) -> bytes:
    return b"\x00\x00" * int(seconds * rate)


def test_a_wav_reports_the_length_it_was_built_with():
    for seconds in (0.5, 1.0, 4.473, 65.25):
        data = to_wav(_pcm(seconds), RATE)
        assert wav_seconds(data) == pytest.approx(seconds, abs=0.001)


def test_the_header_is_the_canonical_forty_four_bytes():
    data = to_wav(_pcm(1.0), RATE)
    assert len(data) == 44 + RATE * 2
    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    assert struct.unpack("<I", data[4:8])[0] == len(data) - 8


def test_stereo_and_eight_bit_are_measured_by_their_own_frame_size():
    stereo = to_wav(b"\x00" * (RATE * 4), RATE, channels=2)
    assert wav_seconds(stereo) == pytest.approx(1.0)
    eight = to_wav(b"\x00" * RATE, RATE, bits=8)
    assert wav_seconds(eight) == pytest.approx(1.0)


def test_an_empty_wav_is_zero_seconds_and_still_a_wav():
    data = to_wav(b"", RATE)
    assert is_wav(data)
    assert wav_seconds(data) == 0.0


def test_anything_that_is_not_a_wav_reads_as_unknown_rather_than_zero():
    """`None` and `0.0` mean different things: one is "this is not audio", the other is
    "this is audio with nothing in it". The narrator refuses on the first and the second is
    a legitimate silence."""
    assert wav_seconds(b"") is None
    assert wav_seconds(b'{"text": "spoken"}') is None
    assert wav_seconds(b"RIFF" + b"\x00" * 40) is None, "RIFF alone is not WAVE"
    assert not is_wav(b'{"mode": "NORMAL"}')


def test_a_truncated_file_is_measured_by_what_is_there_not_by_what_it_claims():
    """A process killed mid-write leaves a header promising a length the file does not
    have. Believing it times a subtitle against speech that was never written."""
    full = to_wav(_pcm(4.0), RATE)
    half = full[: 44 + (len(full) - 44) // 2]
    measured = wav_seconds(half)
    assert measured == pytest.approx(2.0, abs=0.01)


def test_a_chunk_before_the_data_chunk_is_skipped():
    """Real encoders put LIST/INFO chunks between `fmt ` and `data`. Walking the chunks
    rather than assuming a fixed offset is what makes those readable."""
    body = _pcm(1.5)
    fmt = struct.pack("<IHHIIHH", 16, 1, 1, RATE, RATE * 2, 2, 16)
    extra = b"LIST" + struct.pack("<I", 4) + b"INFO"
    data = (
        b"RIFF"
        + struct.pack("<I", 4 + 8 + 16 + len(extra) + 8 + len(body))
        + b"WAVE"
        + b"fmt "
        + fmt
        + extra
        + b"data"
        + struct.pack("<I", len(body))
        + body
    )
    assert wav_seconds(data) == pytest.approx(1.5, abs=0.001)


def test_an_odd_sized_chunk_is_followed_by_a_pad_byte():
    """The RIFF rule that breaks a naive parser: chunk sizes are word-aligned."""
    body = _pcm(1.0)
    fmt = struct.pack("<IHHIIHH", 16, 1, 1, RATE, RATE * 2, 2, 16)
    odd = b"note" + struct.pack("<I", 3) + b"abc" + b"\x00"  # 3 bytes + 1 pad
    data = (
        b"RIFF"
        + struct.pack("<I", 4 + 8 + 16 + len(odd) + 8 + len(body))
        + b"WAVE"
        + b"fmt "
        + fmt
        + odd
        + b"data"
        + struct.pack("<I", len(body))
        + body
    )
    assert wav_seconds(data) == pytest.approx(1.0, abs=0.001)


def test_a_sample_rate_of_zero_is_refused_rather_than_dividing_by_it():
    with pytest.raises(ValueError, match="not a sample rate"):
        to_wav(b"\x00\x00", 0)
