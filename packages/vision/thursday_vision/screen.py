"""Screen understanding and visual reference (§30, V6).

Two jobs that look like one.

**Reading the screen** — what window is in front, what it says, what is selected. This is a
different privacy question from the camera: the owner is already looking at it, and
Thursday reading it is closer to reading over a shoulder than to switching on a camera in
the room. It still only happens with screen permission, and a privacy zone can remove it
from the context package entirely (§68).

**Resolving "this"** — when someone says "Thursday ตรงนี้ผิดอะไร", the word carries no
information at all. The meaning is in where they are pointing, what is selected, what they
were just looking at, and what they said. Fusing those is the whole feature; getting it
wrong means acting on the wrong thing, which is why an unresolved reference asks instead of
guessing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from thursday_core.logging import get_logger

from thursday_vision.ports import BoundingBox, Detection, Frame, TextBlock

log = get_logger(__name__)

#: Below this, a resolution is a guess. Acting on a guess about *which thing* the owner
#: means is worse than asking, because the wrong target is often irreversible.
MIN_REFERENCE_CONFIDENCE = 0.45

#: How far outside an element a *gesture* aim may land and still count as pointing at it,
#: in normalised screen units. A mouse pointer is a measured coordinate and gets no
#: tolerance at all; a hand's aim is estimated from two-dimensional landmarks with no depth
#: (see `HandLandmarks.aim_at`), so demanding it land exactly inside the icon would reject
#: almost every real point. The number is deliberately smaller than a typical desktop icon:
#: near enough to touch, not near enough to reach the next one along.
GESTURE_AIM_TOLERANCE = 0.06


@dataclass
class ScreenElement:
    """One thing on screen that can be referred to."""

    label: str
    box: BoundingBox
    role: str = "element"  # button | field | table | chart | cell | window | text
    text: str = ""
    selected: bool = False

    def describe(self) -> str:
        return f"{self.role} {self.label!r}".strip()


@dataclass
class ScreenReading:
    """What is on screen right now."""

    frame: Frame | None = None
    active_window: str | None = None
    active_app: str | None = None
    url: str | None = None
    elements: list[ScreenElement] = field(default_factory=list)
    text: list[TextBlock] = field(default_factory=list)
    selection: str | None = None
    pointer: tuple[float, float] | None = None

    def visible_text(self) -> str:
        return " ".join(t.text for t in self.text).strip()

    def of_role(self, role: str) -> list[ScreenElement]:
        return [e for e in self.elements if e.role == role]


class ScreenReader:
    """Reads the screen through a source, and optionally an OCR provider.

    OCR is the fallback, not the first choice. When the platform can enumerate real UI
    elements it should: a button the accessibility API named is a button, while a button
    OCR found is a rectangle with a word in it, and only one of those can be clicked
    reliably (§19's control-tier ordering, applied to perception).
    """

    def __init__(self, source: Any = None, ocr: Any = None) -> None:
        self._source = source
        self._ocr = ocr

    async def read(
        self,
        *,
        region: dict[str, int] | None = None,
        elements: list[ScreenElement] | None = None,
        with_ocr: bool = False,
    ) -> ScreenReading:
        if self._source is None:
            return ScreenReading(elements=list(elements or []))

        frame = await self._source.capture(region)
        window = await self._source.active_window()
        text: list[TextBlock] = []
        if with_ocr and self._ocr is not None:
            text = await self._ocr.read(frame)

        return ScreenReading(
            frame=frame,
            active_window=window,
            elements=list(elements or []),
            text=text,
        )


@dataclass
class ResolvedReference:
    """What "this" turned out to mean, and how sure we are."""

    target: str
    confidence: float
    evidence: list[str] = field(default_factory=list)
    element: ScreenElement | None = None
    detection: Detection | None = None

    @property
    def confident(self) -> bool:
        return self.confidence >= MIN_REFERENCE_CONFIDENCE

    def describe(self) -> str:
        return f"{self.target} ({', '.join(self.evidence)})"


class VisualReferenceResolver:
    """Works out what the owner meant by "this".

    Signals, strongest first. The ordering is not arbitrary — it runs from *what the owner
    did deliberately* to *what happens to be nearby*:

    1. **Selection.** They highlighted it. Nothing beats an explicit act.
    2. **Pointer or gesture.** They are pointing at it, now. A mouse pointer must land
       inside the thing; a hand's aim is allowed `GESTURE_AIM_TOLERANCE` of slop, and is
       ranked lower for it.
    3. **A named thing in what they said.** "the total" when a cell is labelled total.
    4. **Sole candidate.** Only one thing is on screen; ambiguity does not arise.
    5. **Most prominent.** The last resort, and the weakest — which is why it alone is not
       enough to clear the confidence floor.
    """

    def resolve(
        self,
        *,
        utterance: str = "",
        screen: ScreenReading | None = None,
        detections: list[Detection] | None = None,
        pointing_at: tuple[float, float] | None = None,
        gesture: Any = None,
    ) -> ResolvedReference | None:
        """Fuse everything available into one target.

        ``gesture`` is a `GestureReading`. Its aim is used only when it is *pointing* and
        confident — a thumbs-up carries no direction, and a low-confidence point is a hand
        the tracker is guessing about. Taking its aim anyway would let noise silently
        outrank the mouse.
        """
        screen = screen or ScreenReading()
        detections = detections or []

        gesture_aim = None
        if gesture is not None:
            aiming = getattr(gesture, "pointing_at", None)
            confident = getattr(gesture, "confidence", 0.0) >= 0.6
            if aiming is not None and confident:
                gesture_aim = aiming

        # An explicit argument wins, then the hand, then whatever the screen last reported.
        pointer = pointing_at or gesture_aim or screen.pointer

        # 1. An explicit selection.
        if screen.selection:
            return ResolvedReference(
                target=screen.selection,
                confidence=0.95,
                evidence=["the owner selected it"],
            )
        selected = [e for e in screen.elements if e.selected]
        if len(selected) == 1:
            return ResolvedReference(
                target=selected[0].describe(),
                confidence=0.92,
                evidence=["selected on screen"],
                element=selected[0],
            )

        # 2. Pointing.
        if pointer is not None:
            under = [e for e in screen.elements if e.box.contains(*pointer)]
            if under:
                closest = min(under, key=lambda e: e.box.distance_to(*pointer))
                how = "pointing at it" if gesture_aim else "the pointer is over it"
                return ResolvedReference(
                    target=closest.describe(),
                    confidence=0.88,
                    evidence=[f"the owner is {how}"],
                    element=closest,
                )
            if gesture_aim is not None:
                near = sorted(
                    (
                        (e.box.distance_to_edge(*gesture_aim), e)
                        for e in screen.elements
                        if e.box.distance_to_edge(*gesture_aim) <= GESTURE_AIM_TOLERANCE
                    ),
                    key=lambda pair: pair[0],
                )
                if len(near) == 1:
                    return ResolvedReference(
                        target=near[0][1].describe(),
                        confidence=0.72,
                        evidence=["the owner is pointing at it", "aim is approximate"],
                        element=near[0][1],
                    )
                if near:
                    # Two things within a fingertip's error of each other. Returned rather
                    # than dropped, and below the floor rather than above it, so the caller
                    # asks "this one?" instead of picking one and being wrong half the time.
                    return ResolvedReference(
                        target=near[0][1].describe(),
                        confidence=0.4,
                        evidence=[
                            "the owner is pointing at it",
                            f"but {len(near) - 1} other thing(s) are just as close",
                        ],
                        element=near[0][1],
                    )
            hit = [d for d in detections if d.box.contains(*pointer)]
            if hit:
                closest_object = min(hit, key=lambda d: d.box.distance_to(*pointer))
                return ResolvedReference(
                    target=closest_object.label,
                    confidence=0.82 * closest_object.confidence + 0.15,
                    evidence=["the owner is pointing at it"],
                    detection=closest_object,
                )

        # 3. Something they named.
        words = {w.strip(".,!?").lower() for w in utterance.split() if len(w) > 2}
        if words:
            for element in screen.elements:
                haystack = f"{element.label} {element.text}".lower()
                if any(word in haystack for word in words):
                    return ResolvedReference(
                        target=element.describe(),
                        confidence=0.7,
                        evidence=["named in the request"],
                        element=element,
                    )
            for detection in detections:
                if detection.label.lower() in words:
                    return ResolvedReference(
                        target=detection.label,
                        confidence=0.7 * detection.confidence + 0.2,
                        evidence=["named in the request"],
                        detection=detection,
                    )

        # 4. Only one thing it could be.
        candidates = len(screen.elements) + len(detections)
        if candidates == 1:
            if screen.elements:
                only = screen.elements[0]
                return ResolvedReference(
                    target=only.describe(),
                    confidence=0.75,
                    evidence=["the only thing on screen"],
                    element=only,
                )
            single = detections[0]
            return ResolvedReference(
                target=single.label,
                confidence=0.6 * single.confidence + 0.25,
                evidence=["the only thing in view"],
                detection=single,
            )

        # 5. Most prominent — weak on purpose. Below the floor, so this alone becomes a
        #    question rather than an action.
        if detections:
            biggest = max(detections, key=lambda d: d.confidence * (0.5 + d.box.area))
            return ResolvedReference(
                target=biggest.label,
                confidence=min(0.44, biggest.confidence * 0.5),
                evidence=["the most prominent thing in view", "no clearer signal"],
                detection=biggest,
            )

        log.debug("visual_reference_unresolved", utterance=utterance[:40])
        return None


# --------------------------------------------------------------------------- annotation


@dataclass
class Annotation:
    """Something drawn over the screen or a frame, to show the owner what Thursday means.

    Kept as data rather than pixels so the same annotation can be drawn by the desktop app,
    described in text for a voice reply, or asserted in a test — and so that "what did it
    highlight?" is answerable after the fact.
    """

    kind: str  # highlight | box | arrow | label | focus
    box: BoundingBox | None = None
    text: str = ""
    colour: str = "#38bdf8"
    to: tuple[float, float] | None = None  # arrows have a destination

    def describe(self) -> str:
        where = ""
        if self.box is not None:
            cx, cy = self.box.centre
            where = f" at ({cx:.0%}, {cy:.0%})"
        return f"{self.kind}{where}: {self.text}".strip(": ")


def highlight(box: BoundingBox, text: str = "") -> Annotation:
    return Annotation(kind="highlight", box=box, text=text)


def bounding_box(box: BoundingBox, text: str = "") -> Annotation:
    return Annotation(kind="box", box=box, text=text)


def arrow(to: tuple[float, float], text: str = "") -> Annotation:
    return Annotation(kind="arrow", to=to, text=text)


def label(box: BoundingBox, text: str) -> Annotation:
    return Annotation(kind="label", box=box, text=text)


def focus(box: BoundingBox, text: str = "") -> Annotation:
    """Dim everything else. The strongest of the five, so it is used sparingly."""
    return Annotation(kind="focus", box=box, text=text)


def annotate(reference: ResolvedReference) -> list[Annotation]:
    """Show what a resolved reference resolved *to*.

    A confident resolution gets a box; an unconfident one gets a box **and** its reasoning
    written next to it, because that is the case where the owner needs to be able to say
    "no, the other one".
    """
    box = None
    if reference.element is not None:
        box = reference.element.box
    elif reference.detection is not None:
        box = reference.detection.box
    if box is None:
        return []

    if reference.confident:
        return [bounding_box(box, reference.target)]
    return [
        bounding_box(box, reference.target),
        label(box, f"is this what you meant? ({', '.join(reference.evidence)})"),
    ]
