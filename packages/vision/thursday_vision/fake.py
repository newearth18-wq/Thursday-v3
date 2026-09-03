"""Test doubles for vision (V6, PART 88).

Shipped rather than test-only, for the same reasons as `FakeDeviceNode` and the voice
fakes: CI has no camera, the desktop app needs a way to demo the pipeline without one, and
anyone extending the providers needs a camera they can make misbehave on demand.

`FakeCamera` counts `open`/`close` calls. That is the detail that matters — the camera
tests are largely about whether the hardware was opened when it should not have been, and a
double that cannot answer that would leave the privacy guarantee untested.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from thursday_vision.ports import (
    Barcode,
    BoundingBox,
    Detection,
    Frame,
    SceneReading,
    TextBlock,
)


def fake_frame(
    *,
    source: str = "camera",
    payload: bytes | None = None,
    camera_id: str = "fake-cam",
    location_context: str | None = "office desk",
) -> Frame:
    return Frame(
        data=payload if payload is not None else b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4,
        source=source,
        width=640,
        height=480,
        camera_id=camera_id,
        location_context=location_context,
    )


def frames_that_differ(count: int, *, source: str = "camera") -> list[Frame]:
    """Frames a change detector will actually see as different.

    The byte *distribution* has to differ, not merely the order. A permutation of the same
    bytes has an identical histogram, and the sampler's signature is a histogram — so a
    fake built from permutations would silently test nothing.
    """
    return [fake_frame(source=source, payload=bytes([(i * 8) % 256]) * 2048) for i in range(count)]


@dataclass
class FakeCamera:
    """A camera that exists only in memory, and remembers whether it was opened."""

    camera_id: str = "fake-cam"
    name: str = "Fake camera"
    opens: int = 0
    closes: int = 0
    captures: int = 0
    is_open: bool = False
    #: Set to raise on close, for exercising the hardware-fails-to-close path.
    fail_close: bool = False
    frames: list[Frame] = field(default_factory=list)

    async def open(self) -> None:
        self.opens += 1
        self.is_open = True

    async def close(self) -> None:
        self.closes += 1
        self.is_open = False
        if self.fail_close:
            raise OSError("the camera would not release")

    async def capture(self) -> Frame:
        if not self.is_open:
            raise RuntimeError("capture from a closed camera")
        self.captures += 1
        if self.frames:
            return self.frames.pop(0)
        return fake_frame(camera_id=self.camera_id)

    async def stream(self) -> AsyncIterator[Frame]:
        while self.is_open:
            yield await self.capture()
            await asyncio.sleep(0)


@dataclass
class FakeScreen:
    """A screen source that hands back a scripted window title and frame."""

    name: str = "fake-screen"
    window: str | None = "grades.xlsx — Excel"
    captures: int = 0

    async def capture(self, region: dict[str, int] | None = None) -> Frame:
        self.captures += 1
        return fake_frame(source="screen", camera_id=None, location_context=None)

    async def active_window(self) -> str | None:
        return self.window


@dataclass
class ScriptedDetector:
    """Returns whatever it was told to, ignoring the frame."""

    name: str = "scripted-detector"
    local: bool = True
    results: list[list[Detection]] = field(default_factory=list)
    default: list[Detection] = field(default_factory=list)
    calls: int = 0

    async def detect(self, frame: Frame) -> list[Detection]:
        self.calls += 1
        return self.results.pop(0) if self.results else list(self.default)


@dataclass
class ScriptedOCR:
    name: str = "scripted-ocr"
    local: bool = True
    blocks: list[TextBlock] = field(default_factory=list)
    calls: int = 0

    async def read(self, frame: Frame, *, languages: list[str] | None = None) -> list[TextBlock]:
        self.calls += 1
        return list(self.blocks)


@dataclass
class ScriptedBarcodes:
    name: str = "scripted-barcodes"
    codes: list[Barcode] = field(default_factory=list)
    calls: int = 0

    async def scan(self, frame: Frame) -> list[Barcode]:
        self.calls += 1
        return list(self.codes)


@dataclass
class FailingDetector:
    """For checking that one broken provider does not take the pipeline with it."""

    name: str = "failing-detector"
    local: bool = True
    attempts: int = 0

    async def detect(self, frame: Frame) -> list[Detection]:
        self.attempts += 1
        raise RuntimeError("the detector crashed")


@dataclass
class ScriptedAnalyzer:
    name: str = "scripted-analyzer"
    local: bool = True
    answer: str = "a book"
    seen: list[SceneReading] = field(default_factory=list)

    async def describe(self, reading: SceneReading, *, question: str = "") -> str:
        self.seen.append(reading)
        return self.answer


def detection(
    label: str,
    confidence: float = 0.9,
    *,
    x: float = 0.3,
    y: float = 0.3,
    width: float = 0.4,
    height: float = 0.4,
    kind: str | None = None,
) -> Detection:
    from thursday_vision.providers import kind_for

    return Detection(
        label=label,
        confidence=confidence,
        kind=kind or kind_for(label),
        box=BoundingBox(x=x, y=y, width=width, height=height),
    )


def text_block(text: str, confidence: float = 0.9, *, area: float = 0.2) -> TextBlock:
    side = max(0.01, area**0.5)
    return TextBlock(
        text=text,
        confidence=confidence,
        box=BoundingBox(x=0.1, y=0.1, width=side, height=side),
    )
