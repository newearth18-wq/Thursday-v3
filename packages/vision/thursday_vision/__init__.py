"""Vision: camera, screen, OCR, detection, spatial memory (V6).

    camera or screen → sample → detect → OCR → barcode → analyse → answer

The camera is off. Everything in this package exists to keep that true in a way the owner
can verify rather than trust — see `camera.py` and ADR 0020.
"""

from thursday_vision.camera import CameraDenied, CameraManager, CameraState
from thursday_vision.ports import (
    Barcode,
    BoundingBox,
    Detection,
    Frame,
    SceneReading,
    TextBlock,
)
from thursday_vision.sampling import FrameSampler, SamplingPolicy
from thursday_vision.screen import (
    Annotation,
    ResolvedReference,
    ScreenElement,
    ScreenReader,
    VisualReferenceResolver,
)
from thursday_vision.service import VisionAnswer, VisionService
from thursday_vision.spatial import Observation, SpatialMemory, TrackedObject

__all__ = [
    "Annotation",
    "Barcode",
    "BoundingBox",
    "CameraDenied",
    "CameraManager",
    "CameraState",
    "Detection",
    "Frame",
    "FrameSampler",
    "Observation",
    "ResolvedReference",
    "SamplingPolicy",
    "SceneReading",
    "ScreenElement",
    "ScreenReader",
    "SpatialMemory",
    "TextBlock",
    "TrackedObject",
    "VisionAnswer",
    "VisionService",
    "VisualReferenceResolver",
]
