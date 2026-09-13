"""Can this machine draw Thai? (Sprint 107)

Asked by rendering, not by reading a font directory. `fc-list` says a font claims a Thai
range; whether libass finds it through fontconfig at render time is the question the tests
actually depend on, and the only way to know is to try — the same principle §12 applies to
verification everywhere else.

A machine without a Thai font is a legitimate machine, so the tests that need one **skip**,
exactly as the media tests skip without ffmpeg and the keychain tests skip without a Secret
Service. What must not happen is CI skipping them silently: CI installs `fonts-tlwg` on
purpose and asserts this returns True before the suite runs (ADR 0074), so a skip there means
the install broke rather than the coverage quietly disappearing — which is precisely the
failure ADR 0081 found had been happening for sprints.
"""

from __future__ import annotations

import subprocess
import tempfile
from functools import cache
from pathlib import Path

from thursday_media.ffmpeg import discover

#: One character is enough to answer the question, and it is the one every Thai string has.
PROBE = "ก"


@cache
def thai_font_available() -> bool:
    """Whether libass can actually draw Thai on this machine, right now."""
    ffmpeg, _ = discover()
    if not ffmpeg:
        return False

    with tempfile.TemporaryDirectory(prefix="thursday-fontprobe-") as workspace:
        work = Path(workspace)
        (work / "subs.srt").write_text(
            f"1\n00:00:00,000 --> 00:00:01,000\n{PROBE}\n", encoding="utf-8"
        )
        try:
            result = subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=black:s=64x64:d=1",
                    "-vf",
                    "subtitles=subs.srt",
                    str(work / "probe.mp4"),
                ],
                cwd=work,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            return False

    return result.returncode == 0 and "failed to find any fallback" not in result.stderr
