"""Vision providers (V6).

Local by default, for the same reason speech is: a frame from a camera in the owner's home
is among the most private things this system handles (§34), and a provider that reaches the
cloud has to be chosen deliberately rather than fallen into.

The shipped adapters are thin wrappers over optional dependencies, imported lazily so a
checkout without them still runs. `packages/vision/fake.py` carries the doubles the tests
and the CLI use.
"""

from __future__ import annotations

import re
from typing import Any

from thursday_core.logging import get_logger

from thursday_vision.ports import Barcode, BoundingBox, Detection, Frame, SceneReading, TextBlock

log = get_logger(__name__)

#: Coarse groupings the spatial memory files sightings under. The list is deliberately
#: short — a taxonomy nobody maintains is a taxonomy that drifts.
OBJECT_KINDS: dict[str, str] = {
    "book": "book",
    "laptop": "computer",
    "computer": "computer",
    "monitor": "computer",
    "keyboard": "computer",
    "mouse": "computer",
    "printer": "equipment",
    "phone": "phone",
    "cell phone": "phone",
    "document": "document",
    "paper": "document",
    "person": "person",
    "box": "container",
    "chair": "furniture",
    "desk": "furniture",
    "table": "furniture",
    "cup": "object",
    "bottle": "object",
    "bag": "container",
    "keys": "object",
}


#: What the owner calls a thing, mapped to what the detector calls it. Detector labels are
#: English (COCO and friends); the owner asks in Thai. Without this, "หนังสืออยู่ไหน" never
#: matches a sighting labelled "book" — a failure that would hit the primary user of this
#: system on every single spatial question.
LABEL_ALIASES: dict[str, str] = {
    "หนังสือ": "book",
    "โน้ตบุ๊ก": "laptop",
    "โน๊ตบุ๊ค": "laptop",
    "คอม": "computer",
    "คอมพิวเตอร์": "computer",
    "จอ": "monitor",
    "คีย์บอร์ด": "keyboard",
    "เมาส์": "mouse",
    "เครื่องพิมพ์": "printer",
    "ปรินเตอร์": "printer",
    "โทรศัพท์": "phone",
    "มือถือ": "phone",
    "เอกสาร": "document",
    "กระดาษ": "paper",
    "คน": "person",
    "กล่อง": "box",
    "เก้าอี้": "chair",
    "โต๊ะ": "desk",
    "แก้ว": "cup",
    "ขวด": "bottle",
    "กระเป๋า": "bag",
    "กุญแจ": "keys",
    "แว่น": "glasses",
    "แว่นตา": "glasses",
}


def canonical_label(label: str) -> str:
    """What the detector would call the thing the owner just named."""
    cleaned = label.strip().lower()
    return LABEL_ALIASES.get(cleaned, cleaned)


def kind_for(label: str) -> str:
    return OBJECT_KINDS.get(canonical_label(label), "object")


class YoloObjectDetector:
    """Ultralytics YOLO, run locally. Imported lazily; heavy."""

    name = "yolo"
    local = True

    def __init__(self, model_path: str = "yolov8n.pt", *, min_confidence: float = 0.35) -> None:
        self.model_path = model_path
        self.min_confidence = min_confidence
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is None:
            from ultralytics import YOLO  # imported lazily; large

            self._model = YOLO(self.model_path)
        return self._model

    async def detect(self, frame: Frame) -> list[Detection]:
        import asyncio
        import io

        def run() -> list[Detection]:
            from PIL import Image

            image = Image.open(io.BytesIO(frame.data))
            width, height = image.size
            found: list[Detection] = []
            for result in self._load().predict(image, verbose=False):
                for box in result.boxes:
                    confidence = float(box.conf[0])
                    if confidence < self.min_confidence:
                        continue
                    label = result.names[int(box.cls[0])]
                    x1, y1, x2, y2 = (float(v) for v in box.xyxy[0])
                    found.append(
                        Detection(
                            label=label,
                            confidence=confidence,
                            kind=kind_for(label),
                            box=BoundingBox(
                                x=x1 / width,
                                y=y1 / height,
                                width=(x2 - x1) / width,
                                height=(y2 - y1) / height,
                            ),
                        )
                    )
            return found

        return await asyncio.to_thread(run)


class TesseractOCR:
    """Tesseract, run locally. The offline tier's reader."""

    name = "tesseract"
    local = True

    def __init__(self, *, min_confidence: float = 0.4) -> None:
        self.min_confidence = min_confidence

    async def read(self, frame: Frame, *, languages: list[str] | None = None) -> list[TextBlock]:
        import asyncio
        import io

        def run() -> list[TextBlock]:
            import pytesseract
            from PIL import Image

            image = Image.open(io.BytesIO(frame.data))
            width, height = image.size
            data = pytesseract.image_to_data(
                image,
                lang="+".join(languages or ["eng", "tha"]),
                output_type=pytesseract.Output.DICT,
            )
            blocks: list[TextBlock] = []
            for index, text in enumerate(data["text"]):
                if not text.strip():
                    continue
                confidence = float(data["conf"][index]) / 100
                if confidence < self.min_confidence:
                    continue
                blocks.append(
                    TextBlock(
                        text=text.strip(),
                        confidence=confidence,
                        box=BoundingBox(
                            x=data["left"][index] / width,
                            y=data["top"][index] / height,
                            width=data["width"][index] / width,
                            height=data["height"][index] / height,
                        ),
                    )
                )
            return blocks

        return await asyncio.to_thread(run)


class ZbarBarcodeReader:
    """QR, EAN, ISBN and Code128, via pyzbar. Local, and fast enough to run on every frame."""

    name = "zbar"
    local = True

    async def scan(self, frame: Frame) -> list[Barcode]:
        import asyncio
        import io

        def run() -> list[Barcode]:
            from PIL import Image
            from pyzbar import pyzbar

            image = Image.open(io.BytesIO(frame.data))
            width, height = image.size
            codes: list[Barcode] = []
            for code in pyzbar.decode(image):
                rect = code.rect
                codes.append(
                    Barcode(
                        value=code.data.decode("utf-8", errors="replace"),
                        kind=code.type,
                        box=BoundingBox(
                            x=rect.left / width,
                            y=rect.top / height,
                            width=rect.width / width,
                            height=rect.height / height,
                        ),
                    )
                )
            return codes

        return await asyncio.to_thread(run)


_ISBN_HINT = re.compile(r"(?i)isbn[\s:-]*([\d-]{10,17})")


class RuleBasedSceneAnalyzer:
    """Describes a scene without a model.

    The offline tier. It reads what the detector and OCR found and says so plainly, which
    is less fluent than a vision model and cannot invent anything — the failure mode that
    matters most when the owner is holding an object up and asking what it is.
    """

    name = "rule-based"
    local = True

    async def describe(self, reading: SceneReading, *, question: str = "") -> str:
        primary = reading.primary
        text = reading.all_text(min_confidence=0.5)
        isbn = next((b.value for b in reading.barcodes if b.looks_like_isbn), None)

        if primary is None and not text and not reading.barcodes:
            return "ผมไม่เห็นอะไรที่ระบุได้ในภาพนี้"

        parts: list[str] = []
        if primary is not None:
            confidence = (
                "" if primary.confidence >= 0.75 else f" (ไม่ค่อยแน่ใจ {primary.confidence:.0%})"
            )
            parts.append(f"น่าจะเป็น{primary.label}{confidence}")

        if text:
            # Quoted, because it is read text rather than Thursday's own words — and
            # because OCR output is untrusted content (ADR 0010).
            parts.append(f'อ่านข้อความได้ว่า "{text[:120]}"')

        if isbn:
            parts.append(f"มีบาร์โค้ด ISBN {isbn}")
        elif reading.barcodes:
            parts.append(f"มีบาร์โค้ด {reading.barcodes[0].kind}: {reading.barcodes[0].value[:60]}")

        return " — ".join(parts)


def title_from_text(blocks: list[TextBlock]) -> str | None:
    """Guess a book or document title from OCR blocks.

    The largest confident block, because a cover sets its title in the biggest type. A
    guess, and labelled as one by the caller — not a lookup.
    """
    usable = [b for b in blocks if b.confidence >= 0.5 and len(b.text.strip()) > 2]
    if not usable:
        return None
    with_boxes = [b for b in usable if b.box is not None]
    if with_boxes:
        return max(with_boxes, key=lambda b: b.box.area).text.strip()
    return max(usable, key=lambda b: len(b.text)).text.strip()


def isbn_from_text(blocks: list[TextBlock]) -> str | None:
    for block in blocks:
        if match := _ISBN_HINT.search(block.text):
            return match.group(1)
    return None
