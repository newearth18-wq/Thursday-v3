"""Teaching in the moment, without becoming a system that nags (§5–§7, §41, §50, §51, §66).

§50 asks for a scored tip engine and §51 for a cooldown. Written naively those two are in
tension — a score high enough always wins, and "occasionally" becomes "whenever the number is
big" — so the structure here keeps them from arguing:

    a ceiling      teaching frequency (§7, §39). OFF means off. Checked first, and no
                   score reaches past it.
    a gate         the existing `ProactivityGate`, which already meters everything else
                   Thursday says unprompted. One meter, not two.
    a cooldown     at most one unsolicited tip per stretch of work (§51), and a dismissed
                   tip does not come back soon (§66).
    a score        only then, and only to choose *which* tip — never whether to speak.

That ordering is the design. A score that could decide *whether* to interrupt is a score
somebody will tune upward; a score that only ranks candidates after three independent gates
have said "you may speak once" can be as enthusiastic as it likes.

**A tip is attached to a moment.** §5 and §41 both say the same thing from different angles:
the tip arrives *after* the work, about the work that just happened, and carries one idea. A
tip that interrupts, or that teaches something unrelated to what the owner is doing, is a
notification with a lesson in it.

**Dismissal is remembered (§66).** The acceptance test is explicit: if the owner says no, do
not keep asking. That is recorded on the capability rather than on the tip, because "not
interested in gestures" is about gestures however many ways Thursday finds to raise them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from thursday_shared.enums import NotificationPriority

from thursday_core.catalogue import FEATURES_BY_KEY, status
from thursday_core.learning import Familiarity, LearningRecord, TeachingFrequency
from thursday_core.logging import get_logger

log = get_logger(__name__)

#: At most one unsolicited tip per stretch of work (§51), whatever the score says.
COOLDOWN = timedelta(minutes=30)

#: A dismissed capability is not raised again for this long. §66: "If dismissed, do not
#: repeatedly ask." Long enough that the answer is respected; not permanent, because
#: "not now" in week one is a different sentence from "never".
DISMISSED_FOR = timedelta(days=30)

#: How many times Thursday may introduce the same capability before it stops. Three
#: introductions with no use is a message that is not landing, and repeating it is nagging.
MAX_INTRODUCTIONS = 3

#: Uses of a capability before Thursday offers to turn it into something better (§6's own
#: example: "คุณใช้ผมหาไฟล์บ่อย … ผมสามารถจำรูปแบบการค้นหานี้เป็น Skill ให้ได้"). Three, which
#: is also §66's acceptance test: "User searches files 3 times."
USES_BEFORE_UPGRADE = 3

#: Below this a tip is not worth the interruption. Applied after the gates, so it is only
#: ever choosing between candidates that were already allowed.
THRESHOLD = 0.35


def _priority_for(frequency: TeachingFrequency) -> NotificationPriority:
    """How loudly a tip competes, given how often the owner asked to be taught.

    This mapping exists because of a bug the first version shipped with. Tips were sent
    through the gate at `LOW`, which reads well — a tip *should* be the first casualty of a
    busy hour. But `ProactivityGate` only lets `LOW` through at proactivity HIGH, and the
    shipped default is NORMAL. So `teaching: normal` in settings.yaml promised occasional
    tips and delivered none, silently, on every default install: two settings that each
    looked right and were wrong together.

    The dials compose instead. Teaching frequency decides *how insistent* a tip is allowed
    to be; proactivity still decides whether Thursday speaks at all, and the gate's hourly
    limit still applies. Turning teaching down really does make tips the first thing cut.
    """
    if frequency <= TeachingFrequency.LOW:
        return NotificationPriority.LOW
    return NotificationPriority.NORMAL


@dataclass(frozen=True)
class Tip:
    """One idea, offered once, about what just happened."""

    capability: str
    text: str
    #: What the owner would say to take it up. §41: one concept, and a way to act on it.
    try_this: str = ""
    score: float = 0.0
    #: Why this was chosen. For the decision journal and for a developer asking "why that?".
    reason: str = ""

    def render(self) -> dict:
        return {
            "capability": self.capability,
            "text": self.text,
            "try": self.try_this,
            # A tip is always dismissible, and the client needs to know what to send back.
            "dismiss": self.capability,
        }


#: What Thursday offers to teach after a capability has been used a few times — §5's "next
#: time you could just say…" and §6's upgrade offer. Keyed on the capability just used, so
#: the tip is about the work that actually happened.
_AFTER_USING: dict[str, tuple[str, str, str]] = {
    # capability used -> (capability taught, text, try_this)
    "file_search": (
        "skills",
        "คุณให้ผมหาไฟล์บ่อย — ถ้าต้องการ ผมจำรูปแบบการค้นหานี้ไว้เป็นขั้นตอน แล้วครั้งหน้าเรียกด้วยประโยคเดียวได้",
        "จำวิธีหาไฟล์แบบนี้ไว้",
    ),
    "open_app": (
        "automation",
        "งานที่ทำซ้ำทุกวัน ผมตั้งให้ทำเองตามเวลาได้ครับ",
        "ทำแบบนี้ทุกเช้า",
    ),
    "conversation": (
        "memory",
        "ถ้ามีอะไรที่อยากให้ผมจำไว้ใช้ครั้งหน้า บอกได้เลยครับ",
        "Thursday จำไว้ว่า…",
    ),
    "memory": (
        "multi_step",
        "งานใหญ่ ๆ คุณบอกผลลัพธ์ที่ต้องการได้เลย ผมแบ่งงานและตรวจผลให้เอง",
        "วิเคราะห์ไฟล์นี้ แล้วเขียนสรุปให้หน่อย",
    ),
}


class TipEngine:
    """Decides whether Thursday says something teaching-shaped, and which thing.

    Holds no schedule and no timer: it is asked, at the end of a piece of work, whether
    there is anything worth saying. Nothing here can start a conversation on its own.
    """

    def __init__(self, record: LearningRecord, *, gate: Any = None) -> None:
        self._record = record
        # The same gate that meters every other unprompted thing Thursday says. Not a second
        # rate limiter — a tip and a notification compete for the same scarce resource,
        # which is the owner's attention, and metering them separately means each is
        # reasonable alone and together they are constant.
        self._gate = gate
        self._last_tip: datetime | None = None

    # ------------------------------------------------------------------ the gates

    def may_speak(self, *, now: datetime | None = None) -> tuple[bool, str]:
        """Whether an unsolicited tip is permissible at all, before anything is scored."""
        now = now or datetime.now(UTC)

        # §7/§39 first, and absolutely. OFF means off.
        if not self._record.may_teach_unprompted():
            return False, f"teaching is {self._record.frequency.name}"

        if self._last_tip is not None and now - self._last_tip < COOLDOWN:
            return False, "a tip was offered recently"

        if self._gate is not None:
            allowed, why = self._gate.allows(_priority_for(self._record.frequency), now=now)
            if not allowed:
                return False, why

        return True, "allowed"

    # ------------------------------------------------------------------ choosing

    def after(
        self,
        container: Any,
        *,
        capability: str,
        succeeded: bool = True,
        now: datetime | None = None,
    ) -> Tip | None:
        """A piece of work just finished. Is there one thing worth saying about it?

        Returns at most one tip, or None — which is the common and correct answer. §41: one
        concept, after the work, never interrupting it.
        """
        now = now or datetime.now(UTC)

        if not succeeded:
            # Somebody who just watched something fail is not in the mood to be taught a
            # different feature, and a tip here reads as changing the subject.
            return None

        allowed, why = self.may_speak(now=now)
        if not allowed:
            log.debug("tip_suppressed", capability=capability, reason=why)
            return None

        candidate = self._candidate_for(container, capability, now=now)
        if candidate is None or candidate.score < THRESHOLD:
            return None

        self._last_tip = now
        self._record.introduced(candidate.capability, now=now)
        log.info(
            "tip_offered",
            after=capability,
            teaching=candidate.capability,
            score=round(candidate.score, 3),
        )
        return candidate

    def _candidate_for(self, container: Any, used: str, *, now: datetime) -> Tip | None:
        mapping = _AFTER_USING.get(used)
        if mapping is None:
            return None
        capability, text, try_this = mapping

        score, reason = self.score(container, capability, after=used, now=now)
        if score <= 0:
            return None
        return Tip(capability=capability, text=text, try_this=try_this, score=score, reason=reason)

    # ------------------------------------------------------------------ §50 scoring

    def score(
        self, container: Any, capability: str, *, after: str = "", now: datetime | None = None
    ) -> tuple[float, str]:
        """§50's terms, as a number and the sentence explaining it.

        Only ever chooses *which* tip. Whether to speak was settled by `may_speak` before
        anything reached here, which is what keeps a high score from becoming a reason to
        interrupt.
        """
        now = now or datetime.now(UTC)
        entry = self._record.entry(capability)

        # Feature availability. Not a weight — a veto. Teaching a camera to a machine with
        # no camera is §12's failure however relevant it would otherwise be.
        row = status(container, capability)
        if row is None or not row.usable:
            return 0.0, "not available on this machine"

        # §66. A dismissal is about the capability, not about the wording, so no rephrasing
        # gets past it.
        if entry.dismissed and (
            entry.last_taught is None or now - entry.last_taught < DISMISSED_FOR
        ):
            return 0.0, "the owner dismissed this"

        # Repeating a message that is not landing is nagging.
        if entry.introductions >= MAX_INTRODUCTIONS:
            return 0.0, "already introduced several times"

        familiarity = self._record.knows(capability, now=now)
        if familiarity >= Familiarity.LEARNED:
            return 0.0, "the owner already knows this"

        score = 0.5
        reasons = []

        # Relevance: earned by the owner having actually done the thing this follows from,
        # repeatedly. §6's example is "คุณใช้ผมหาไฟล์บ่อย" — the *frequency* is the reason
        # the offer is welcome rather than random.
        if after:
            uses = self._record.entry(after).uses
            if uses < USES_BEFORE_UPGRADE:
                # A veto, not a small penalty, and the reason is in the tip's own words: it
                # says "คุณใช้ผมหาไฟล์บ่อย". Offered after one search that sentence is simply
                # untrue, and a tip whose text is false is worse than no tip. §6's premise is
                # frequency, and §66 names the number.
                return 0.0, f"has used {after} only {uses} time(s)"
            score += 0.3
            reasons.append(f"used {after} {uses} times")

        # Never mentioned beats mentioned-once: the first time is the informative one.
        if familiarity is Familiarity.NOT_DISCOVERED:
            score += 0.15
            reasons.append("never mentioned")

        # Expected benefit, crudely: a shallower capability helps sooner.
        feature = FEATURES_BY_KEY.get(capability)
        if feature is not None:
            score += max(0.0, (7 - feature.depth)) * 0.02

        return min(score, 1.0), "; ".join(reasons)

    # ------------------------------------------------------------------ §66 dismissal

    def dismiss(self, capability: str, *, now: datetime | None = None) -> None:
        """The owner said no. Recorded against the capability so no rephrasing gets past it."""
        self._record.dismissed(capability, now=now)


# --------------------------------------------------------------------------- §27 errors


#: What to say when something did not work *because of a setting the owner can change*.
#: §27's rule: an error is a teaching moment, and the teaching is the next step rather than
#: the diagnosis. Keyed on the capability that was refused.
_WHEN_REFUSED: dict[str, tuple[str, str]] = {
    "vision": (
        "กล้องยังไม่ได้รับอนุญาตครับ",
        "เปิดได้ที่ ความเป็นส่วนตัว → กล้อง หรือให้ผมพาไปตั้งค่าตอนนี้ก็ได้",
    ),
    "gesture": (
        "การสั่งงานด้วยท่าทางยังปิดอยู่ครับ",
        "เปิดได้ในหน้าการตั้งค่า แล้วลองใหม่ได้เลย",
    ),
    "open_app": (
        "ยังไม่มีเครื่องที่ผมสั่งงานได้ครับ",
        "ติดตั้ง Thursday บนเครื่องที่ต้องการควบคุม แล้วผมจะทำให้ได้ทันที",
    ),
    "multi_device": (
        "ตอนนี้มีเครื่องเชื่อมอยู่เครื่องเดียวครับ",
        "เชื่อมอีกเครื่องแล้วสั่งข้ามเครื่องได้เลย",
    ),
}


def teach_from_error(container: Any, capability: str) -> dict | None:
    """§27. Turn a refusal into the next step rather than a dead end.

    Not a tip and not gated by teaching frequency: the owner asked for something and did not
    get it, so telling them why and what to do about it is answering, not teaching. §39's
    ceiling is about Thursday speaking *unprompted*.
    """
    words = _WHEN_REFUSED.get(capability)
    if words is None:
        return None
    problem, fix = words
    row = status(container, capability)
    return {
        "problem": problem,
        "fix": fix,
        # Where the real reason came from, if it differs — the catalogue knows about
        # hardware, this table knows about wording.
        "detail": row.availability.reason if row and not row.usable else "",
        "capability": capability,
    }
