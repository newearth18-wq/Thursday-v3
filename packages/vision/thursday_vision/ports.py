"""The vision ports (V6).

What Thursday needs from anything that can see: frames, objects in them, text in them, and
a reading of the whole scene. Each is a Protocol with a local adapter and a fake, so the
pipeline above is testable with no camera, no model weights and no display.

The contracts carry two things that are easy to leave out and expensive to add later.

**Provenance.** Every `Frame` says where it came from and when. An observation whose source
is unknown cannot be reasoned about — "I saw your keys on the desk" means something
different if the frame came from the owner's laptop webcam than from a shared room camera,
and the difference is not recoverable after the fact.

**Uncertainty.** Detections and text blocks carry confidence, and nothing downstream is
allowed to drop it. A vision system that reports what it thinks it saw with the same
assurance as what it actually read is a vision system that will confidently misidentify
something at the worst moment.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID


@dataclass(frozen=True)
class BoundingBox:
    """Normalised to 0-1, so a box survives being resized or re-encoded."""

    x: float
    y: float
    width: float
    height: float

    @property
    def centre(self) -> tuple[float, float]:
        return self.x + self.width / 2, self.y + self.height / 2

    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)

    def contains(self, x: float, y: float) -> bool:
        return self.x <= x <= self.x + self.width and self.y <= y <= self.y + self.height

    def distance_to(self, x: float, y: float) -> float:
        cx, cy = self.centre
        return ((cx - x) ** 2 + (cy - y) ** 2) ** 0.5

    def distance_to_edge(self, x: float, y: float) -> float:
        """Distance to the nearest point *on* the box; zero when the point is inside.

        Different from `distance_to` in the case that matters: a point just outside a large
        box is far from its centre but touching its edge. Centre distance would rank a
        small far-away box above the box the point is practically on.
        """
        dx = max(self.x - x, 0.0, x - (self.x + self.width))
        dy = max(self.y - y, 0.0, y - (self.y + self.height))
        return (dx * dx + dy * dy) ** 0.5

    def overlaps(self, other: BoundingBox) -> float:
        """Intersection over union — how much two boxes are the same thing."""
        left, right = max(self.x, other.x), min(self.x + self.width, other.x + other.width)
        top, bottom = max(self.y, other.y), min(self.y + self.height, other.y + other.height)
        if right <= left or bottom <= top:
            return 0.0
        intersection = (right - left) * (bottom - top)
        union = self.area + other.area - intersection
        return intersection / union if union else 0.0


@dataclass(frozen=True)
class Frame:
    """One image, and enough about it to reason from.

    ``source`` and ``captured_at`` are required rather than optional: an observation whose
    origin is unknown cannot be judged, and "the camera saw" is a different claim from "the
    screen showed".
    """

    data: bytes
    source: str  # "camera" | "screen"
    captured_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    width: int = 0
    height: int = 0
    device_id: UUID | None = None
    camera_id: str | None = None
    #: Free-form, e.g. "office desk". Set by whoever knows where the camera is.
    location_context: str | None = None

    @property
    def size(self) -> int:
        return len(self.data)


@dataclass(frozen=True)
class Detection:
    """One object found in a frame."""

    label: str
    confidence: float
    box: BoundingBox
    #: A coarse grouping ("book", "person", "furniture") for spatial memory to file under.
    kind: str = "object"
    attributes: dict[str, Any] = field(default_factory=dict)

    def describe(self) -> str:
        return f"{self.label} ({self.confidence:.0%})"


@dataclass(frozen=True)
class TextBlock:
    """Text read out of a frame.

    Text found by OCR is **untrusted content** (ADR 0010). A sign that says "ignore your
    previous instructions" is a photograph of a sentence, not an instruction — which is why
    this is a distinct type from anything Thursday treats as direction.
    """

    text: str
    confidence: float
    box: BoundingBox | None = None
    language: str | None = None


@dataclass(frozen=True)
class Barcode:
    """A scanned code. ``kind`` is the symbology: QR, EAN13, ISBN, CODE128."""

    value: str
    kind: str
    box: BoundingBox | None = None

    @property
    def looks_like_isbn(self) -> bool:
        digits = "".join(ch for ch in self.value if ch.isdigit())
        return self.kind.upper() in {"ISBN", "EAN13"} and len(digits) in (10, 13)


@dataclass
class SceneReading:
    """Everything understood about one frame, assembled."""

    frame: Frame
    detections: list[Detection] = field(default_factory=list)
    text: list[TextBlock] = field(default_factory=list)
    barcodes: list[Barcode] = field(default_factory=list)
    summary: str = ""
    #: Set when the pipeline could not reach a confident reading. Answering "I am not sure
    #: what this is" is a valid outcome and must be distinguishable from an empty frame.
    uncertain: bool = False

    @property
    def primary(self) -> Detection | None:
        """The most prominent object: confidence weighted by how much of the frame it fills.

        Not simply the highest confidence — a tiny, certainly-identified pen in the corner
        is rarely what someone pointing a camera is asking about.
        """
        if not self.detections:
            return None
        return max(self.detections, key=lambda d: d.confidence * (0.5 + d.box.area))

    def all_text(self, *, min_confidence: float = 0.0) -> str:
        return " ".join(t.text for t in self.text if t.confidence >= min_confidence).strip()


@runtime_checkable
class ObjectDetector(Protocol):
    name: str
    local: bool

    async def detect(self, frame: Frame) -> list[Detection]: ...


@runtime_checkable
class OCRProvider(Protocol):
    name: str
    local: bool

    async def read(
        self, frame: Frame, *, languages: list[str] | None = None
    ) -> list[TextBlock]: ...


@runtime_checkable
class BarcodeReader(Protocol):
    name: str

    async def scan(self, frame: Frame) -> list[Barcode]: ...


@runtime_checkable
class SceneAnalyzer(Protocol):
    """Turns detections and text into a sentence a person would say."""

    name: str
    local: bool

    async def describe(self, reading: SceneReading, *, question: str = "") -> str: ...


@runtime_checkable
class CameraSource(Protocol):
    """A camera. Opening one is a privileged act — see `CameraManager`."""

    camera_id: str
    name: str

    async def open(self) -> None: ...
    async def close(self) -> None: ...
    async def capture(self) -> Frame: ...
    def stream(self) -> AsyncIterator[Frame]: ...


@runtime_checkable
class ScreenSource(Protocol):
    """The owner's screen, which is a different privacy question from a camera: they are
    already looking at it, and Thursday reading it is closer to reading over a shoulder
    than to switching on a camera in the room."""

    name: str

    async def capture(self, region: dict[str, int] | None = None) -> Frame: ...
    async def active_window(self) -> str | None: ...
