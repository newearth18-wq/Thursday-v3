"""The quality gate for media artefacts (§15, V11).

The brief asks for a validation step over every artefact, and names what it should check for
media: dimensions, audio, missing subtitles, corrupt output. This is that step, and it is
the code that turns "ffmpeg exited zero" into "the file contains what was asked for" — the
distinction rule 1 of the README exists for.

Three states, not two. `pass` and `fail` are obvious; `unknown` is the one that earns its
keep. A probe that could not read the file tells you nothing about whether the render was
good, and reporting that as a pass is exactly the lie this whole project is organised
against. `QualityReport.ok` is false for `unknown`, and `certain` says which kind of false
it is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from thursday_media.ports import ExportPreset, MediaProbe
from thursday_media.subtitles import SubtitleTrack

#: Below this, a file is not a video that came out short — it is a container with nothing
#: in it. x264 writes a valid header before it writes a frame, so a killed render leaves
#: exactly this behind.
MIN_PLAUSIBLE_BYTES = 1024


@dataclass(frozen=True)
class Check:
    name: str
    state: str  # pass | fail | unknown
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.state == "pass"


@dataclass
class QualityReport:
    """What was checked, what was found, and how sure it is."""

    path: str = ""
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True only when every check passed. An `unknown` is not a pass."""
        return bool(self.checks) and all(c.state == "pass" for c in self.checks)

    @property
    def certain(self) -> bool:
        """False when anything could not be determined, whatever the verdict."""
        return all(c.state != "unknown" for c in self.checks)

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.state == "fail"]

    @property
    def unknowns(self) -> list[Check]:
        return [c for c in self.checks if c.state == "unknown"]

    def describe(self) -> str:
        if self.ok:
            return f"{len(self.checks)} checks passed"
        parts = [f"{c.name}: {c.detail}" for c in self.failures]
        parts += [f"{c.name}: could not tell — {c.detail}" for c in self.unknowns]
        return "; ".join(parts) or "nothing was checked"

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "ok": self.ok,
            "certain": self.certain,
            "summary": self.describe(),
            "checks": [{"name": c.name, "state": c.state, "detail": c.detail} for c in self.checks],
        }


def check_output(
    probe: MediaProbe,
    *,
    target: ExportPreset | None = None,
    expect_audio: bool = False,
    expect_video: bool = True,
    subtitles: SubtitleTrack | None = None,
    min_seconds: float | None = None,
    max_seconds: float | None = None,
) -> QualityReport:
    """Judge a rendered file against what it was supposed to be.

    `probe` comes from the editor. Pass the `target` preset to check dimensions, a
    `SubtitleTrack` to check that the video is long enough to hold every cue, and
    `expect_audio` when narration or music was part of the plan.
    """
    report = QualityReport(path=probe.path)
    add = report.checks.append

    # ---- corrupt output. First, because every check below is meaningless without it.
    if probe.size_bytes <= 0:
        add(Check("not_corrupt", "fail", "the file is empty"))
        return report
    if probe.size_bytes < MIN_PLAUSIBLE_BYTES:
        add(
            Check(
                "not_corrupt",
                "fail",
                f"{probe.size_bytes} bytes is a container header and no content",
            )
        )
        return report
    if not probe.streams:
        add(Check("not_corrupt", "fail", "no streams could be read from the file"))
        return report
    add(Check("not_corrupt", "pass", f"{probe.size_bytes} bytes, {len(probe.streams)} streams"))

    # ---- video stream and dimensions
    video = probe.video
    if expect_video:
        if video is None:
            add(Check("has_video", "fail", "no video stream"))
        else:
            add(Check("has_video", "pass", video.codec or "video stream present"))

        if target is not None:
            if video is None or video.width is None or video.height is None:
                add(Check("dimensions", "unknown", "the frame size could not be read"))
            elif (video.width, video.height) == (target.width, target.height):
                add(Check("dimensions", "pass", f"{video.width}×{video.height}"))
            else:
                add(
                    Check(
                        "dimensions",
                        "fail",
                        f"{video.width}×{video.height}, expected "
                        f"{target.width}×{target.height} ({target.aspect})",
                    )
                )

    # ---- audio
    if expect_audio:
        audio = probe.audio
        if audio is None:
            add(Check("has_audio", "fail", "narration or music was expected and there is none"))
        elif audio.channels == 0:
            add(Check("has_audio", "fail", "an audio stream with no channels"))
        else:
            add(Check("has_audio", "pass", f"{audio.codec or 'audio'}, {audio.channels or '?'}ch"))

    # ---- duration
    if probe.seconds is None:
        if min_seconds is not None or max_seconds is not None or subtitles is not None:
            add(Check("duration", "unknown", "the duration could not be read"))
    else:
        if min_seconds is not None and probe.seconds + 0.05 < min_seconds:
            add(Check("duration", "fail", f"{probe.seconds:.2f}s, shorter than {min_seconds:.2f}s"))
        elif max_seconds is not None and probe.seconds > max_seconds + 0.05:
            add(Check("duration", "fail", f"{probe.seconds:.2f}s, longer than {max_seconds:.2f}s"))
        elif min_seconds is not None or max_seconds is not None:
            add(Check("duration", "pass", f"{probe.seconds:.2f}s"))

    # ---- subtitles
    if subtitles is not None:
        if not subtitles.cues:
            add(Check("subtitles", "fail", "a subtitle track was expected and it has no cues"))
        elif probe.seconds is None:
            add(
                Check(
                    "subtitles",
                    "unknown",
                    "the video duration could not be read, so a cue past the end "
                    "cannot be ruled out",
                )
            )
        elif subtitles.seconds > probe.seconds + 0.5:
            # A cue that starts after the video ends is a cue nobody will ever see, and it
            # is the usual symptom of narration timings estimated against the wrong script.
            add(
                Check(
                    "subtitles",
                    "fail",
                    f"the last cue ends at {subtitles.seconds:.2f}s, "
                    f"past the end of a {probe.seconds:.2f}s video",
                )
            )
        elif not subtitles.measured:
            # Not a failure. Estimated timings are legitimate output — they are simply not
            # the same claim as synchronised ones, and the owner is told which they have.
            add(
                Check(
                    "subtitles",
                    "pass",
                    f"{len(subtitles.cues)} cues, timed by reading speed rather than "
                    "measured against the narration",
                )
            )
        else:
            add(Check("subtitles", "pass", f"{len(subtitles.cues)} cues, synced to the audio"))

    return report
