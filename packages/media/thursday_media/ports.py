"""The media editing port, and the vocabulary it speaks (ADR 0060).

One protocol, two adapters, and a rule that runs through both: **an edit never writes over
its input**. Every operation takes a destination and produces a new file, so "undo" for a
media edit is the original file still being exactly where it was. That is cheaper and far
more trustworthy than a versioning scheme, and it is why none of these operations declare a
backup requirement — there is nothing to back up.

The probe types deliberately use `None` for *unknown* rather than `0`. A video whose
duration could not be read is not a video of length zero, and the quality checker in
`quality.py` treats the two completely differently: unknown is a reason to refuse to judge,
zero is a reason to fail the artefact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from thursday_shared.errors import ThursdayError


class MediaError(ThursdayError):
    code = "media_error"


class MediaUnavailable(MediaError):
    """No editing backend on this machine.

    Carries the remedy, not just the fact. This is the error behind the "not installed"
    state the UI shows, and a message like "media editing is unavailable" with no next step
    is how a capability gap becomes a support question.
    """

    code = "media_unavailable"
    retryable = False


class OperationFailed(MediaError):
    """The backend ran and refused, or produced nothing usable.

    Retryable is False on purpose: ffmpeg is deterministic. The same command against the
    same input fails the same way the second time, and a recovery ladder that retries it is
    a ladder that wastes a minute before reporting the error it already had.
    """

    code = "media_operation_failed"
    retryable = False


@dataclass(frozen=True)
class StreamInfo:
    """One stream inside a container. Absent fields mean *not known*, never zero."""

    kind: str = "unknown"  # video | audio | subtitle
    codec: str = ""
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    sample_rate: int | None = None
    channels: int | None = None

    @property
    def pixels(self) -> int | None:
        if self.width is None or self.height is None:
            return None
        return self.width * self.height


@dataclass(frozen=True)
class MediaProbe:
    """What a backend could read about a file.

    This is *evidence*, in the sense rule 1 of the README means it: a render is reported as
    finished because the output was probed and found to contain what was asked for, not
    because ffmpeg exited zero. `quality.check_output` is the code that makes that judgement
    and this is what it judges.
    """

    path: str = ""
    seconds: float | None = None
    container: str = ""
    size_bytes: int = 0
    streams: tuple[StreamInfo, ...] = ()

    @property
    def video(self) -> StreamInfo | None:
        return next((s for s in self.streams if s.kind == "video"), None)

    @property
    def audio(self) -> StreamInfo | None:
        return next((s for s in self.streams if s.kind == "audio"), None)

    @property
    def has_video(self) -> bool:
        return self.video is not None

    @property
    def has_audio(self) -> bool:
        return self.audio is not None

    def describe(self) -> str:
        # No streams means nothing could be read, and "silent" would be a claim about a
        # file this probe knows nothing about. An unreadable file is not a quiet one.
        if not self.streams:
            return "unreadable"
        bits: list[str] = []
        video = self.video
        if video and video.width and video.height:
            bits.append(f"{video.width}×{video.height}")
            if video.codec:
                bits.append(video.codec)
        if self.seconds is not None:
            bits.append(f"{self.seconds:.1f}s")
        bits.append("with audio" if self.has_audio else "silent")
        return ", ".join(bits)


@dataclass(frozen=True)
class ExportPreset:
    """A shape to export in. The three the brief names, plus whatever a caller builds."""

    name: str
    width: int
    height: int
    fps: int = 30
    #: Constant Rate Factor. Lower is better quality and a bigger file; 23 is x264's own
    #: default and a sane place to sit for material that will be watched once on a phone.
    crf: int = 23
    audio_bitrate: str = "128k"

    @property
    def aspect(self) -> str:
        divisor = _gcd(self.width, self.height)
        return f"{self.width // divisor}:{self.height // divisor}"

    @property
    def is_portrait(self) -> bool:
        return self.height > self.width


def _gcd(a: int, b: int) -> int:
    while b:
        a, b = b, a % b
    return a or 1


#: The presets the brief asks for by name. Keyed by aspect because that is what somebody
#: asking for a video actually says — "make it vertical for TikTok" is `9:16`.
PRESETS: dict[str, ExportPreset] = {
    "16:9": ExportPreset("landscape", 1920, 1080),
    "9:16": ExportPreset("portrait", 1080, 1920),
    "1:1": ExportPreset("square", 1080, 1080),
}

#: What somebody is likely to say, mapped to the aspect they mean.
_ALIASES: dict[str, str] = {
    "landscape": "16:9",
    "wide": "16:9",
    "youtube": "16:9",
    "horizontal": "16:9",
    "portrait": "9:16",
    "vertical": "9:16",
    "tiktok": "9:16",
    "reels": "9:16",
    "shorts": "9:16",
    "story": "9:16",
    "square": "1:1",
    "instagram": "1:1",
}


def preset(name: str) -> ExportPreset:
    """Resolve an aspect or a nickname to a preset.

    Raises rather than defaulting. A caller who asked for "4:3" and silently got 16:9 would
    discover it after the render, which is the expensive end of the pipeline to find a
    mistake at.
    """
    key = (name or "").strip().lower()
    key = _ALIASES.get(key, key)
    if key not in PRESETS:
        known = ", ".join(sorted(PRESETS) + sorted(_ALIASES))
        raise OperationFailed(f"no export preset called {name!r} — known presets: {known}")
    return PRESETS[key]


@dataclass(frozen=True)
class Overlay:
    """An image laid on top of a video. Position is named, not measured.

    Pixel coordinates would be the more flexible interface and the wrong one: a caller
    working in coordinates has to know the output size, which it does not know until the
    preset is resolved, and a logo at (1750, 60) lands off-screen the moment somebody
    exports the same project vertically.
    """

    image: str
    corner: str = "top-right"  # top-left | top-right | bottom-left | bottom-right | center
    margin: int = 32
    #: 0.0 fully transparent, 1.0 opaque.
    opacity: float = 1.0
    start: float = 0.0
    end: float | None = None


@runtime_checkable
class MediaEditor(Protocol):
    """What Thursday can do to a media file on this machine.

    Implementations must honour two invariants, and both are tested against every adapter
    in `tests/unit/test_media_ports_v11.py`:

    1. `available` tells the truth *before* work is attempted, so the UI can show "not
       installed" instead of a failure after a progress bar.
    2. No method writes to a path it was given as an input.
    """

    name: str
    available: bool

    async def probe(self, path: str) -> MediaProbe: ...

    async def trim(self, src: str, dst: str, *, start: float, end: float | None = None) -> str: ...

    async def concat(self, sources: list[str], dst: str) -> str: ...

    async def resize(self, src: str, dst: str, *, target: ExportPreset) -> str: ...

    async def still(self, image: str, dst: str, *, seconds: float, target: ExportPreset) -> str: ...

    async def burn_subtitles(self, src: str, dst: str, *, subtitles: str) -> str: ...

    async def dub(
        self,
        src: str,
        dst: str,
        *,
        narration: str | None = None,
        music: str | None = None,
        music_gain_db: float = -18.0,
    ) -> str: ...

    async def normalize_audio(self, src: str, dst: str) -> str: ...

    async def strip_silence(self, src: str, dst: str, *, threshold_db: float = -45.0) -> str: ...

    async def overlay(self, src: str, dst: str, *, overlays: list[Overlay]) -> str: ...

    async def crossfade(self, a: str, b: str, dst: str, *, seconds: float = 0.5) -> str: ...

    async def thumbnail(self, src: str, dst: str, *, at: float = 0.0) -> str: ...

    def health(self) -> dict[str, object]: ...


@dataclass
class Capability:
    """One thing an editor can or cannot do, with the reason when it cannot.

    Exists so the UI can render a capability list rather than a binary. An ffmpeg build
    without libx264 can still trim and concatenate; saying "media editing unavailable"
    because one encoder is missing would be both wrong and unhelpful.
    """

    name: str
    ok: bool
    detail: str = ""


@dataclass
class EditorHealth:
    backend: str = "none"
    available: bool = False
    version: str = ""
    detail: str = ""
    capabilities: list[Capability] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "backend": self.backend,
            "available": self.available,
            "version": self.version,
            "detail": self.detail,
            "capabilities": [
                {"name": c.name, "ok": c.ok, "detail": c.detail} for c in self.capabilities
            ],
        }
