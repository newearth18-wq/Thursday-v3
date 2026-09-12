"""The editor Thursday uses when there is no editor (ADR 0060).

This class exists so that "media editing is not installed on this machine" is a *state the
system is in* rather than an exception raised from somewhere deep in a render. It satisfies
`MediaEditor` completely and refuses every operation with the same sentence, which names the
remedy.

It is the direct expression of the rule the brief states hardest: never show fake progress
and then report a completion that did not happen. A container built on a machine without
ffmpeg holds one of these, `health()` reports `available: false`, the UI shows "not
installed", and any call raises before a single frame is written.
"""

from __future__ import annotations

from typing import Any

from thursday_media.ports import EditorHealth, ExportPreset, MediaProbe, MediaUnavailable, Overlay

#: Said once, here, so every refusal says the same thing and says what to do about it.
REASON = (
    "Media editing is not available on this machine: ffmpeg is not installed. "
    "Install ffmpeg (or `pip install imageio-ffmpeg`) and restart Thursday, or set "
    "THURSDAY_FFMPEG_PATH to an existing ffmpeg. Nothing was changed."
)


class UnavailableEditor:
    """Refuses everything, immediately, with a reason and a remedy."""

    name = "unavailable"
    available = False

    def __init__(self, reason: str = REASON) -> None:
        self.reason = reason

    def _refuse(self, operation: str) -> MediaUnavailable:
        return MediaUnavailable(self.reason, operation=operation)

    async def probe(self, path: str) -> MediaProbe:
        # Probing is the one operation that could *almost* be honoured — `agents/media.py`
        # reads headers without ffmpeg. But this class's contract is that it does nothing,
        # and a probe that silently returned an empty result would read as "the file is
        # unreadable" rather than "Thursday cannot read it here", which is a different
        # thing to tell somebody.
        raise self._refuse("probe")

    async def trim(self, src: str, dst: str, *, start: float, end: float | None = None) -> str:
        raise self._refuse("trim")

    async def concat(self, sources: list[str], dst: str) -> str:
        raise self._refuse("concat")

    async def resize(self, src: str, dst: str, *, target: ExportPreset) -> str:
        raise self._refuse("resize")

    async def still(self, image: str, dst: str, *, seconds: float, target: ExportPreset) -> str:
        raise self._refuse("still")

    async def burn_subtitles(self, src: str, dst: str, *, subtitles: str) -> str:
        raise self._refuse("burn_subtitles")

    async def dub(
        self,
        src: str,
        dst: str,
        *,
        narration: str | None = None,
        music: str | None = None,
        music_gain_db: float = -18.0,
    ) -> str:
        raise self._refuse("dub")

    async def normalize_audio(self, src: str, dst: str) -> str:
        raise self._refuse("normalize_audio")

    async def strip_silence(self, src: str, dst: str, *, threshold_db: float = -45.0) -> str:
        raise self._refuse("strip_silence")

    async def overlay(self, src: str, dst: str, *, overlays: list[Overlay]) -> str:
        raise self._refuse("overlay")

    async def crossfade(self, a: str, b: str, dst: str, *, seconds: float = 0.5) -> str:
        raise self._refuse("crossfade")

    async def thumbnail(self, src: str, dst: str, *, at: float = 0.0) -> str:
        raise self._refuse("thumbnail")

    def health(self) -> dict[str, Any]:
        return EditorHealth(backend="none", available=False, detail=self.reason).to_dict()
