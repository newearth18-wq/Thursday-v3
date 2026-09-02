"""VisionService — the seeing pipeline (V6).

    camera or screen → sample → detect → OCR → barcode → analyse → answer

One object owns the order, because the order is a privacy decision as much as an
engineering one. Detection runs **locally, first**, and its result is what decides whether
a frame is worth anything further. Nothing reaches a cloud analyser that a local model has
not already flagged as interesting, and nothing reaches anything at all without a camera
grant (§51).

A provider that fails degrades the reading rather than losing it. Someone holding a book up
to a camera should get "I can read the cover but could not identify the object" rather than
an error — a partial answer is useful and an exception is not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from thursday_core.logging import get_logger
from thursday_shared.models import Event

from thursday_vision.camera import CameraDenied, CameraManager
from thursday_vision.ports import Frame, SceneReading
from thursday_vision.providers import isbn_from_text, title_from_text
from thursday_vision.sampling import FrameSampler
from thursday_vision.spatial import SpatialMemory

log = get_logger(__name__)


@dataclass
class VisionAnswer:
    """What Thursday says about something it looked at."""

    text: str
    reading: SceneReading | None = None
    #: Set when the pipeline could not identify anything with confidence. Distinct from an
    #: empty frame, and phrased differently to the owner.
    uncertain: bool = False
    refused: str | None = None

    @property
    def ok(self) -> bool:
        return self.refused is None


class VisionService:
    """Looks at things, and only when allowed to."""

    def __init__(
        self,
        *,
        camera: CameraManager | None = None,
        screen: Any = None,
        detector: Any = None,
        ocr: Any = None,
        barcodes: Any = None,
        analyzer: Any = None,
        sampler: FrameSampler | None = None,
        spatial: SpatialMemory | None = None,
        bus: Any = None,
    ) -> None:
        self.camera = camera
        self._screen = screen
        self._detector = detector
        self._ocr = ocr
        self._barcodes = barcodes
        self._analyzer = analyzer
        self.sampler = sampler or FrameSampler()
        self.spatial = spatial or SpatialMemory()
        self._bus = bus

    # ------------------------------------------------------------------ reading a frame

    async def read_frame(self, frame: Frame, *, question: str = "") -> SceneReading:
        """Detect, read and scan one frame. Each stage is independently survivable."""
        reading = SceneReading(frame=frame)

        reading.detections = await self._safely(
            "detect", lambda: self._detector.detect(frame), self._detector, []
        )
        reading.text = await self._safely("ocr", lambda: self._ocr.read(frame), self._ocr, [])
        reading.barcodes = await self._safely(
            "barcode", lambda: self._barcodes.scan(frame), self._barcodes, []
        )

        # Nothing recognised, no text, no code — say so as its own outcome rather than
        # producing a confident sentence about an empty result.
        reading.uncertain = not (reading.detections or reading.text or reading.barcodes)

        if self._analyzer is not None:
            reading.summary = await self._safely(
                "analyse",
                lambda: self._analyzer.describe(reading, question=question),
                self._analyzer,
                "",
            )

        self._remember(reading)
        return reading

    async def _safely(self, stage: str, call: Any, provider: Any, fallback: Any) -> Any:
        """One failed provider degrades the reading; it does not lose it."""
        if provider is None:
            return fallback
        try:
            return await call()
        except Exception as exc:
            log.warning("vision_stage_failed", stage=stage, error=str(exc))
            return fallback

    def _remember(self, reading: SceneReading) -> None:
        """File what was seen as *sightings* — never as facts about the present (§25)."""
        frame = reading.frame
        for detection in reading.detections:
            if detection.confidence < 0.5:
                continue
            self.spatial.record(
                detection.label,
                confidence=detection.confidence,
                location_context=frame.location_context if frame else None,
                device_id=frame.device_id if frame else None,
                bbox={
                    "x": detection.box.x,
                    "y": detection.box.y,
                    "width": detection.box.width,
                    "height": detection.box.height,
                },
                object_type=detection.kind,
                camera_id=frame.camera_id if frame else None,
            )

    # ------------------------------------------------------------------ the camera path

    async def look(self, question: str = "", *, reason: str | None = None) -> VisionAnswer:
        """ "Thursday นี่คืออะไร" — look through the camera and answer.

        The grant is checked, never created. A component that can grant itself permission
        to use a camera has no permission model at all; asking is the caller's job, which
        in practice means the approval flow or the owner's own words.
        """
        if self.camera is None:
            return VisionAnswer(text="", refused="no camera is configured")

        allowed, why = self.camera.may_capture()
        if not allowed:
            return VisionAnswer(text="", refused=why)

        try:
            frame = await self.camera.capture()
        except CameraDenied as exc:
            return VisionAnswer(text="", refused=str(exc))

        reading = await self.read_frame(frame, question=question)
        await self._emit("vision.observed", reading)

        if reading.uncertain:
            return VisionAnswer(
                text="ผมมองไม่ออกว่านี่คืออะไร ลองขยับให้ชัดขึ้นหรือใกล้ขึ้นได้ไหม",
                reading=reading,
                uncertain=True,
            )
        return VisionAnswer(text=reading.summary, reading=reading)

    async def identify(self, question: str = "") -> VisionAnswer:
        """Identify a held-up object, using every signal at once.

        A book is the case worth designing for: the detector says "book", OCR reads the
        cover, and a barcode gives an ISBN. Each alone is a guess; together they are an
        answer, and the reply says which parts came from where so the owner can tell a read
        title from a recognised shape.
        """
        answer = await self.look(question)
        if not answer.ok or answer.reading is None:
            return answer

        reading = answer.reading
        title = title_from_text(reading.text)
        isbn = isbn_from_text(reading.text) or next(
            (b.value for b in reading.barcodes if b.looks_like_isbn), None
        )
        primary = reading.primary

        parts: list[str] = []
        if primary is not None:
            parts.append(f"เป็น{primary.label}")
        if title:
            parts.append(f'บนปกเขียนว่า "{title}"')
        if isbn:
            parts.append(f"ISBN {isbn}")

        if not parts:
            return answer
        return VisionAnswer(text=" — ".join(parts), reading=reading)

    # ------------------------------------------------------------------ the screen path

    async def read_screen(self, *, question: str = "") -> SceneReading:
        """Read the screen. A different privacy question from the camera: the owner is
        already looking at it (§30)."""
        if self._screen is None:
            return SceneReading(frame=None, uncertain=True)
        frame = await self._screen.capture(None)
        return await self.read_frame(frame, question=question)

    # ------------------------------------------------------------------ streaming

    async def watch(self, frames: Any, *, limit: int = 100) -> list[SceneReading]:
        """Sample a stream and read only what survives.

        The stream is consumed here and the frames are discarded as they go. What comes out
        is a handful of readings, which is the only form in which anything Thursday saw is
        allowed to persist (§52).
        """
        readings: list[SceneReading] = []
        seen = 0
        async for frame in frames:
            seen += 1
            if seen > limit:
                break
            detections = await self._safely(
                "detect", lambda f=frame: self._detector.detect(f), self._detector, []
            )
            if not self.sampler.consider(frame, detections):
                continue
            readings.append(await self.read_frame(frame))
        log.info("vision_watch_finished", **self.sampler.stats())
        return readings

    # ------------------------------------------------------------------ reporting

    async def _emit(self, kind: str, reading: SceneReading) -> None:
        if self._bus is None:
            return
        await self._bus.publish(
            Event(
                kind=kind,
                payload={
                    # Labels and counts, never the frame. What Thursday saw is a fact about
                    # the owner's home; the picture of it does not go on an event bus.
                    "objects": [d.label for d in reading.detections],
                    "text_blocks": len(reading.text),
                    "barcodes": len(reading.barcodes),
                    "uncertain": reading.uncertain,
                },
            )
        )

    def snapshot(self) -> dict:
        return {
            "camera": self.camera.snapshot() if self.camera else {"state": "OFF"},
            "detector": getattr(self._detector, "name", None),
            "ocr": getattr(self._ocr, "name", None),
            "barcodes": getattr(self._barcodes, "name", None),
            "analyzer": getattr(self._analyzer, "name", None),
            "sampling": self.sampler.stats(),
            "observations": len(self.spatial),
        }
