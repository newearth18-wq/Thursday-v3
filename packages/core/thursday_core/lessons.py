"""Lessons, and the rule that they end when the machine proves it (§2–§4, §16, §17, §49, §60).

§4 gives the loop — SHOW → TRY → VERIFY → NEXT — and the third step is the one that decides
whether this is a tutorial or a slideshow.

**VERIFY means an observation, not a reply.** A lesson step is complete when the thing the
owner was asked to do actually happened on the machine: Notepad is open, the file was found,
the memory was written. Not when they typed something that looked right, and not when a model
judged their answer plausible. This is ADR 0012 applied to teaching, and it is the same rule
Sprint 64 put on the setup wizard for the same reason — a tutorial that congratulates somebody
on a step that did not work has taught them a thing that does not happen, and they find out at
the moment they first needed it.

So `Step.verify` is a predicate over evidence, and there is **no parameter anywhere on this
module through which a caller can assert that a step succeeded**. A completion flag a client
can post is a completion flag a client will post.

**Lessons are inert data, checked against the live catalogue.** A lesson names the capability
it teaches; whether it can run here comes from `catalogue` (§52), never from the lesson file.
That is why a lesson for a camera cannot be offered on a machine with no camera even though
the lesson is installed and enabled.

**Everything here is local (§60).** Definitions, examples and walkthroughs are Python
literals in this file. No lesson fetches anything, and the tutor works with the network down
— which is the state a lot of first runs are actually in.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from thursday_core.catalogue import FEATURES_BY_KEY, status
from thursday_core.learning import Familiarity, LearningRecord, TutorialStatus
from thursday_core.logging import get_logger

log = get_logger(__name__)


class Stage(StrEnum):
    """§16's learning path. Named for what the owner is doing, not "LEVEL 4".

    The spec allows either and warns against making it feel like a game
    ("ไม่ต้องใช้คำว่า Level ใน UI ถ้าทำให้ดูเป็นเกมเกินไป"), so the game-flavoured spelling
    simply does not exist here — there is no level number to leak into a screen.
    """

    START = "START"
    EVERYDAY = "EVERYDAY"
    ADVANCED = "ADVANCED"
    POWER = "POWER"


STAGE_TITLES: dict[Stage, str] = {
    Stage.START: "เริ่มต้น",
    Stage.EVERYDAY: "ใช้งานประจำวัน",
    Stage.ADVANCED: "ทำงานขั้นสูง",
    Stage.POWER: "ใช้งานเต็มความสามารถ",
}


@dataclass(frozen=True)
class Step:
    """One SHOW → TRY → VERIFY (§4).

    `verify` reads the evidence a step leaves behind and answers whether it happened. It is
    given the container and the result of whatever the owner's attempt produced; it returns a
    bool. Nothing else in this module can mark a step done.
    """

    key: str
    #: SHOW. What Thursday says before the owner tries.
    show: str
    #: TRY. The exact words they could say. §28: an example, never a required syntax.
    try_this: str = ""
    #: NEXT. Said once VERIFY passed — and this is where a step generalises from the one
    #: thing they just did to the shape of things they can now do (§4's own example).
    then: str = ""
    #: The observation that decides it. May be async — reading a store usually is. Default:
    #: nothing happened, so a step whose author forgot to say how to check it never passes.
    verify: Callable[[Any, Any], Any] = lambda _container, _evidence: False
    #: A step the owner may pass without doing — reading, not doing.
    informational: bool = False


@dataclass(frozen=True)
class Lesson:
    """§49's `tutorials` row, as data rather than a table.

    `capability` is the join to the catalogue: it is how "can this run here?" is answered
    without this file knowing anything about cameras or devices.
    """

    id: str
    name: str
    #: The catalogue key this teaches. Availability is read from there, never stored here.
    capability: str
    stage: Stage
    steps: tuple[Step, ...]
    #: Roughly how long, in the only unit a person cares about.
    minutes: int = 1
    enabled: bool = True
    #: Other lessons that make more sense first. Ordering, not gating (§58).
    after: tuple[str, ...] = ()

    def step(self, index: int) -> Step | None:
        return self.steps[index] if 0 <= index < len(self.steps) else None


# ------------------------------------------------------------------------- verifications
#
# Each reads the machine. None of them reads an assertion from a caller.


def _said_something(_container: Any, evidence: Any) -> bool:
    """The owner spoke to Thursday and got a real turn back."""
    return bool(
        getattr(evidence, "reply", None) or (isinstance(evidence, dict) and evidence.get("reply"))
    )


def _app_is_open(container: Any, evidence: Any) -> bool:
    """An app is open because the node says so, not because the command returned.

    The same distinction Sprint 64 made for setup and ADR 0012 made for every device action:
    `ok` means the node did not raise, `verified` means somebody looked.
    """
    if evidence is None:
        return False
    ok = bool(getattr(evidence, "ok", False) or (isinstance(evidence, dict) and evidence.get("ok")))
    verified = bool(
        getattr(evidence, "verified", False)
        or (isinstance(evidence, dict) and evidence.get("verified"))
    )
    return ok and verified


def _found_a_file(_container: Any, evidence: Any) -> bool:
    results = getattr(evidence, "results", None)
    if results is None and isinstance(evidence, dict):
        results = evidence.get("results")
    return bool(results)


async def _memory_written(container: Any, evidence: Any) -> bool:
    """Two halves, and both are needed.

    The turn has to *claim* it wrote a memory, and the store has to *have* it. Checking only
    the claim believes a reply that said "จำแล้วครับ"; checking only the store passes on a
    memory written last week by something else. The conjunction is the actual observation.
    """
    memory = getattr(container, "memory", None)
    if memory is None:
        return False
    memory_id = getattr(evidence, "memory_id", None)
    if memory_id is None and isinstance(evidence, dict):
        memory_id = evidence.get("memory_id")
    if memory_id is None:
        return False
    try:
        return await memory.get(memory_id) is not None
    except Exception:  # pragma: no cover - a store that cannot be read has not stored
        return False


def _acknowledged(_container: Any, evidence: Any) -> bool:
    """For informational steps: the owner said they had read it."""
    return bool(evidence)


# ------------------------------------------------------------------------------ lessons
#
# §17's five basic lessons, plus the safety one §56 wants taught early.


LESSONS: tuple[Lesson, ...] = (
    Lesson(
        id="say-something",
        name="พูดกับ Thursday",
        capability="conversation",
        stage=Stage.START,
        minutes=1,
        steps=(
            Step(
                key="talk",
                show="ลองบอกผมด้วยภาษาปกติว่าอยากให้ช่วยอะไร ไม่ต้องจำคำสั่งครับ",
                try_this="สวัสดี Thursday",
                then=("แบบนี้เลยครับ คุณพูดกับผมได้เหมือนคุยกับคน ไม่ต้องพูดให้ถูกรูปแบบ ผมจะพยายามเข้าใจเอง"),
                verify=_said_something,
            ),
        ),
    ),
    Lesson(
        id="how-to-stop",
        name="สั่งให้ผมหยุด",
        capability="stop_everything",
        stage=Stage.START,
        minutes=1,
        # Second lesson, before anything that touches the machine. §56 names it as an early
        # lesson and the ordering is the substance: knowing how to stop something is what
        # makes it safe to try the things that do something.
        after=("say-something",),
        steps=(
            Step(
                key="stop",
                show=(
                    "ก่อนอย่างอื่น — ถ้าต้องการให้ผมหยุดทุกอย่างทันที "
                    "พูดว่า “Thursday หยุดทั้งหมด” ได้ตลอดเวลา ไม่ว่าผมกำลังทำอะไรอยู่"
                ),
                try_this="Thursday หยุดทั้งหมด",
                then="จำไว้แค่นี้ก็พอครับ ปุ่มนี้ใช้ได้เสมอ และไม่ต้องรอให้ผมถาม",
                verify=_acknowledged,
                informational=True,
            ),
        ),
    ),
    Lesson(
        id="open-an-app",
        name="เปิดโปรแกรม",
        capability="open_app",
        stage=Stage.START,
        minutes=1,
        after=("how-to-stop",),
        steps=(
            Step(
                key="open",
                show="ลองให้ผมเปิดโปรแกรมบนเครื่องนี้ดูครับ",
                try_this="Thursday เปิด Notepad",
                # §4's own closing line: generalise from the one thing to the shape.
                then=("เรียบร้อยครับ แบบนี้คุณสั่งเปิดโปรแกรมอื่นได้เหมือนกัน เช่น Chrome, Word หรือ Excel"),
                verify=_app_is_open,
            ),
        ),
    ),
    Lesson(
        id="find-a-file",
        name="หาไฟล์",
        capability="file_search",
        stage=Stage.EVERYDAY,
        minutes=2,
        after=("open-an-app",),
        steps=(
            Step(
                key="search",
                show="บอกผมว่าไฟล์นั้นเกี่ยวกับอะไร ไม่ต้องรู้ว่าอยู่โฟลเดอร์ไหนครับ",
                try_this="หาไฟล์ Excel ที่แก้ล่าสุด",
                then="ครั้งหน้าพูดสั้น ๆ ได้เลย เช่น “เปิดไฟล์คะแนนล่าสุด” ผมจะหาแล้วเปิดให้ในคำสั่งเดียว",
                verify=_found_a_file,
            ),
        ),
    ),
    Lesson(
        id="ask-about-screen",
        name="ถามเกี่ยวกับหน้าจอ",
        capability="screen_context",
        stage=Stage.EVERYDAY,
        minutes=1,
        after=("open-an-app",),
        steps=(
            Step(
                key="ask",
                show="ผมดูหน้าจอให้ได้ ถ้าคุณขอ — ลองถามว่าตอนนี้เปิดอะไรอยู่ครับ",
                try_this="Thursday ตอนนี้ฉันกำลังเปิดอะไรอยู่",
                then="ถามเรื่องบนหน้าจอได้แบบนี้ เช่น ให้ช่วยอ่าน สรุป หรืออธิบายสิ่งที่เห็น",
                verify=_app_is_open,
            ),
        ),
    ),
    Lesson(
        id="remember-this",
        name="ให้ผมจำข้อมูล",
        capability="memory",
        stage=Stage.EVERYDAY,
        minutes=2,
        after=("say-something",),
        steps=(
            Step(
                key="remember",
                show="บอกให้ผมจำอะไรก็ได้ที่จะใช้อีกในอนาคตครับ",
                try_this="Thursday จำไว้ว่าฉันชอบรายงานแบบสั้น",
                # §18's distinction, said once, in the moment it makes sense.
                then=(
                    "ผมจำไว้แล้วครับ และจะใช้มันในงานครั้งต่อไปเอง "
                    "ถ้าอยากดูว่าผมจำอะไรไว้บ้าง ถามว่า “นายจำอะไรเกี่ยวกับฉันบ้าง” ได้เลย"
                ),
                verify=_memory_written,
            ),
        ),
    ),
)

LESSONS_BY_ID: dict[str, Lesson] = {lesson.id: lesson for lesson in LESSONS}


#: §3's quick introduction: four or five basics, never the whole product. These are lesson
#: ids rather than prose so the intro cannot drift from the lessons it promises.
QUICK_INTRO: tuple[str, ...] = (
    "say-something",
    "how-to-stop",
    "open-an-app",
    "find-a-file",
    "remember-this",
)


# ------------------------------------------------------------------------------ offering


@dataclass
class Offer:
    """A lesson Thursday could teach now, and why it is being offered."""

    lesson: Lesson
    reason: str = ""

    def render(self) -> dict:
        return {
            "id": self.lesson.id,
            "name": self.lesson.name,
            "stage": self.lesson.stage.value,
            "stage_title": STAGE_TITLES[self.lesson.stage],
            "minutes": self.lesson.minutes,
            "reason": self.reason,
        }


def runnable(container: Any, lesson: Lesson) -> bool:
    """Whether this lesson can actually be taught on this machine (§12, §52).

    Asks the catalogue rather than the lesson. A lesson is installed and enabled and still
    cannot run, and that is the correct outcome — teaching a camera to a machine without one
    is the failure §12 is written to prevent.
    """
    if not lesson.enabled:
        return False
    row = status(container, lesson.capability)
    return bool(row and row.usable)


def blocked_reason(container: Any, lesson: Lesson) -> str:
    from thursday_core.catalogue import unavailable_reason

    return unavailable_reason(container, lesson.capability)


def available(container: Any, record: LearningRecord) -> list[Lesson]:
    """Lessons worth offering: runnable here, not finished, not already known."""
    out = []
    for lesson in LESSONS:
        if not runnable(container, lesson):
            continue
        if record.progress(lesson.id).finished:
            continue
        if record.knows(lesson.capability) >= Familiarity.LEARNED:
            # They can already do it. Teaching it anyway is how a tutor becomes noise.
            continue
        out.append(lesson)
    return out


def next_lesson(container: Any, record: LearningRecord) -> Offer | None:
    """The single best next thing to learn (§32, §57).

    One, not a list. §57's "อย่าสอนทุกอย่างวันแรก" is a statement about pacing, and the
    honest implementation of pacing is returning one item.

    Ordered by stage, then by whether its prerequisites are done, then by declaration order.
    Prerequisites *sort* rather than gate: §58 is explicit that nothing is really locked, so
    a lesson whose predecessor was skipped drops down the list and stays reachable.
    """
    candidates = available(container, record)
    if not candidates:
        return None

    def rank(lesson: Lesson) -> tuple:
        unmet = sum(1 for prior in lesson.after if not record.progress(prior).finished)
        stage_order = list(Stage).index(lesson.stage)
        return (unmet, stage_order, LESSONS.index(lesson))

    best = min(candidates, key=rank)
    return Offer(lesson=best, reason=_why(container, record, best))


def _why(container: Any, record: LearningRecord, lesson: Lesson) -> str:
    feature = FEATURES_BY_KEY.get(lesson.capability)
    if feature is None:  # pragma: no cover - lessons name catalogue keys
        return ""
    if record.knows(lesson.capability) >= Familiarity.TRIED:
        return f"คุณเคยใช้ {feature.title} แล้ว — อันนี้จะทำให้ใช้ได้คล่องขึ้น"
    return feature.summary


def path(container: Any, record: LearningRecord) -> list[dict]:
    """§16 and §42: the whole path, with what is done and what is not.

    Shows lessons that cannot run here too, with the reason — hiding them would mean the
    owner never learns that Thursday could do more with a camera or a second machine
    attached, which is a thing they might want to know.
    """
    out = []
    for stage in Stage:
        rows = []
        for lesson in LESSONS:
            if lesson.stage is not stage:
                continue
            progress = record.progress(lesson.id)
            row: dict[str, Any] = {
                "id": lesson.id,
                "name": lesson.name,
                "minutes": lesson.minutes,
                "done": progress.status is TutorialStatus.COMPLETED,
                "available": runnable(container, lesson),
            }
            if not row["available"]:
                row["reason"] = blocked_reason(container, lesson)
            rows.append(row)
        if rows:
            out.append({"stage": stage.value, "title": STAGE_TITLES[stage], "lessons": rows})
    return out


# ------------------------------------------------------------------------------- running


@dataclass(frozen=True)
class StepResult:
    """What happened when the owner tried a step."""

    lesson_id: str
    step: str
    passed: bool
    #: What Thursday says next: the NEXT line if it worked, the SHOW line again if not.
    message: str
    #: Whether the lesson is now finished.
    done: bool = False
    #: The next step's SHOW line, when there is one.
    next_show: str = ""
    next_try: str = ""


class LessonRunner:
    """Drives one lesson through SHOW → TRY → VERIFY → NEXT (§4).

    **There is no way to tell this object that a step succeeded.** `attempt` takes the
    evidence an attempt produced and asks the step's own `verify` what to make of it; there is
    no `passed=`, no `force=`, no `skip_verification=`. That is the same shape Sprint 64 gave
    the setup wizard and ADR 0033 gave the updater, and for the same reason: a completion flag
    a caller can set is a completion flag a caller will set, eventually, from a retry handler
    at three in the morning.

    The one exception is `skip`, which is the owner declining — and it records a *skip*, never
    a completion. §9 keeps those in separate lists precisely so nobody can confuse them later.
    """

    def __init__(self, record: LearningRecord) -> None:
        self._record = record

    # -------------------------------------------------------------- SHOW

    def start(self, container: Any, lesson_id: str, *, now: Any = None) -> StepResult | None:
        """Begin a lesson, if it can run here."""
        lesson = LESSONS_BY_ID.get(lesson_id)
        if lesson is None:
            return None
        if not runnable(container, lesson):
            # Told rather than hidden, with §12's reason attached.
            return StepResult(
                lesson_id=lesson_id,
                step="",
                passed=False,
                message=blocked_reason(container, lesson) or "ยังใช้บทเรียนนี้บนเครื่องนี้ไม่ได้",
            )
        progress = self._record.start(lesson_id, now=now)
        step = lesson.step(progress.current_step) or lesson.steps[0]
        self._record.introduced(lesson.capability, now=now)
        return StepResult(
            lesson_id=lesson_id,
            step=step.key,
            passed=False,
            message=step.show,
            next_show=step.show,
            next_try=step.try_this,
        )

    # -------------------------------------------------------------- TRY → VERIFY → NEXT

    async def attempt(
        self, container: Any, lesson_id: str, evidence: Any, *, now: Any = None
    ) -> StepResult | None:
        """Judge one attempt by what it left behind.

        `evidence` is whatever the owner's action produced — a conversation turn, a device
        result, a search result. It is *data to be checked*, not a verdict to be accepted.
        """
        lesson = LESSONS_BY_ID.get(lesson_id)
        if lesson is None:
            return None
        progress = self._record.progress(lesson_id)
        step = lesson.step(progress.current_step)
        if step is None:
            return StepResult(lesson_id=lesson_id, step="", passed=True, message="", done=True)

        passed = await _run_verify(step, container, evidence)

        if not passed:
            # Not a failure to report — a step to try again. §27: an error is a teaching
            # moment, and the SHOW line is the teaching.
            log.info("lesson_step_not_yet", lesson=lesson_id, step=step.key)
            return StepResult(
                lesson_id=lesson_id,
                step=step.key,
                passed=False,
                message=step.show,
                next_show=step.show,
                next_try=step.try_this,
            )

        self._record.advance(lesson_id, step.key, now=now)
        # The owner did the thing. That is the only event that moves familiarity, and it is
        # recorded as a *use* rather than as an introduction (Sprint 67's distinction).
        self._record.used(lesson.capability, ok=True, now=now)

        following = lesson.step(self._record.progress(lesson_id).current_step)
        if following is None:
            self._record.complete(lesson_id, now=now)
            return StepResult(
                lesson_id=lesson_id, step=step.key, passed=True, message=step.then, done=True
            )
        return StepResult(
            lesson_id=lesson_id,
            step=step.key,
            passed=True,
            message=step.then,
            next_show=following.show,
            next_try=following.try_this,
        )

    def skip(self, lesson_id: str, *, now: Any = None) -> StepResult | None:
        """The owner declines. Recorded as skipped, never as done (§9, §2's "ข้ามก่อน")."""
        lesson = LESSONS_BY_ID.get(lesson_id)
        if lesson is None:
            return None
        progress = self._record.progress(lesson_id)
        step = lesson.step(progress.current_step)
        if step is not None:
            self._record.skip_step(lesson_id, step.key, now=now)
        self._record.skip(lesson_id, now=now)
        return StepResult(
            lesson_id=lesson_id,
            step=step.key if step else "",
            passed=False,
            message="ได้ครับ ข้ามไปก่อน ถ้าอยากกลับมาเรียนบอกผมได้ตลอด",
            done=True,
        )


async def _run_verify(step: Step, container: Any, evidence: Any) -> bool:
    """Call a step's check, awaiting it when it needs to read a store."""
    try:
        result = step.verify(container, evidence)
        if hasattr(result, "__await__"):
            result = await result
        return bool(result)
    except Exception as exc:  # pragma: no cover - a broken check has not verified anything
        log.warning("lesson_verify_failed", step=step.key, error=str(exc))
        return False
