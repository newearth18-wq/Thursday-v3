"""What Thursday can do on *this* machine, in the owner's words (§11, §12, §33, §52, §61).

Every teaching surface reads this module: the Learning Center's sections (§10), the answer to
"นายทำอะไรได้บ้าง" (§33), the tip engine's shortlist (§50), and the lessons' prerequisites
(§12). It exists so there is exactly one place that knows the user-facing vocabulary, and
exactly one rule about where availability comes from.

**Availability is derived, never declared.** This is the spec's own instruction (§52: *"Tutor
obtains capabilities from same registry as Thursday Core"*) and it is the difference between a
tutor and a brochure. Every `Feature` carries a `probe` — a function of the live container —
rather than an `enabled` flag somebody has to remember to flip. A flag would be right on the
day it was written and wrong on the first day a camera was unplugged, and the failure lands on
a beginner being told to try something their machine cannot do (§12).

**A feature with no sentence written for a person is not taught.** Sprint 65's allowlist rule,
applied to features: `AgentSpec.description` is written for whoever wired the agent, and
falling back to it would put "runs web research via the configured SearxNG instance" in front
of somebody who has never heard of SearxNG. So the vocabulary here is declared, agents may
supply their own through `user_description` (§61), and anything with neither is silent.

**Grouped, and small.** §33 is explicit that "what can you do" must not answer with a hundred
items. The catalogue is organised into a handful of areas, the answer names the areas, and
depth comes from the owner asking a second question.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from thursday_core.logging import get_logger

log = get_logger(__name__)


class Availability(StrEnum):
    """Why a feature can or cannot be used here, right now."""

    AVAILABLE = "AVAILABLE"
    #: The software is here; a machine to run it on is not (§12's "ยังไม่พบ").
    NEEDS_DEVICE = "NEEDS_DEVICE"
    #: The machine lacks the hardware — no camera, no microphone.
    NEEDS_HARDWARE = "NEEDS_HARDWARE"
    #: Present and possible, but the owner has not allowed it (§26, §27).
    NEEDS_PERMISSION = "NEEDS_PERMISSION"
    #: Not built, not installed, or switched off in configuration.
    UNAVAILABLE = "UNAVAILABLE"

    @property
    def usable(self) -> bool:
        return self is Availability.AVAILABLE


class Area(StrEnum):
    """§10's Learning Center sections, and §33's grouping. Deliberately few."""

    BASICS = "BASICS"
    COMPUTER = "COMPUTER"
    FILES = "FILES"
    MEMORY = "MEMORY"
    AGENTS = "AGENTS"
    AUTOMATION = "AUTOMATION"
    SEEING = "SEEING"
    DEVICES = "DEVICES"
    SAFETY = "SAFETY"


#: What each area is called in front of a person (§10 names most of these outright).
AREA_TITLES: dict[Area, str] = {
    Area.BASICS: "เริ่มต้นใช้งาน",
    Area.COMPUTER: "การใช้คอมพิวเตอร์",
    Area.FILES: "ไฟล์และเอกสาร",
    Area.MEMORY: "ความจำ",
    Area.AGENTS: "งานหลายขั้นตอน",
    Area.AUTOMATION: "ทำงานอัตโนมัติ",
    Area.SEEING: "กล้องและท่าทาง",
    Area.DEVICES: "หลายอุปกรณ์",
    Area.SAFETY: "ความปลอดภัยและการอนุญาต",
}


@dataclass(frozen=True)
class Unavailable:
    """Why something cannot be used, and what to do instead.

    `alternative` is §12's actual requirement. "เครื่องนี้ยังไม่พบกล้อง" is a dead end;
    "…แต่ใช้กล้องจากมือถือที่เชื่อมกับ Thursday ได้" is the same truth with a way forward,
    and the difference is whether the owner stops here.
    """

    state: Availability
    reason: str
    alternative: str = ""


AVAILABLE = Unavailable(state=Availability.AVAILABLE, reason="")


@dataclass(frozen=True)
class Feature:
    """One thing the owner can do, described for them and checked against the machine."""

    key: str
    area: Area
    title: str
    summary: str
    #: Phrases the owner could actually say. Not syntax — §28's whole point is that there
    #: is none — but examples are how somebody learns the shape of a request.
    examples: tuple[str, ...] = ()
    #: The live check. Returns `AVAILABLE` or an `Unavailable` carrying a reason the owner
    #: can act on. A function rather than a flag, so it cannot be right-once and wrong after.
    probe: Callable[[Any], Unavailable] = lambda _c: AVAILABLE
    #: Roughly where this sits in the learning path (§16). Ordering, not gating — §58 is
    #: explicit that nothing is actually locked.
    depth: int = 1
    safety_notes: str = ""

    def availability(self, container: Any) -> Unavailable:
        try:
            return self.probe(container)
        except Exception as exc:  # pragma: no cover - a probe must never break the answer
            # A catalogue that raises is a Learning Center that fails to open. Unknown is
            # reported as unavailable, which is the safe direction: it declines to teach
            # something rather than teaching something that is not there.
            log.warning("capability_probe_failed", feature=self.key, error=str(exc))
            return Unavailable(state=Availability.UNAVAILABLE, reason="ยังตรวจสอบส่วนนี้ไม่ได้")


# --------------------------------------------------------------------------- the probes
#
# Each reads the same object Thursday Core reads. None of them consults a list of features.


def _always(_container: Any) -> Unavailable:
    return AVAILABLE


def _needs_device(container: Any) -> Unavailable:
    """Anything that touches an OS needs a node attached (§12)."""
    if container.hub is not None and container.hub.online():
        return AVAILABLE
    return Unavailable(
        state=Availability.NEEDS_DEVICE,
        reason="ยังไม่มีเครื่องที่เชื่อมต่ออยู่",
        alternative="ติดตั้ง Thursday บนเครื่องที่ต้องการให้ผมควบคุม แล้วผมจะสั่งงานให้ได้",
    )


def _needs_camera(container: Any) -> Unavailable:
    """A camera the machine does not have, and the mobile answer §12 asks for by name."""
    camera = getattr(container, "camera", None)
    if camera is None:
        return Unavailable(state=Availability.UNAVAILABLE, reason="เครื่องนี้ยังไม่รองรับกล้อง")
    if not _any_device_supports(container, "camera"):
        return Unavailable(
            state=Availability.NEEDS_HARDWARE,
            reason="เครื่องนี้ยังไม่พบกล้อง",
            # §12's example, close to verbatim, because it is the difference between a dead
            # end and a next step.
            alternative="คุณใช้กล้องจากมือถือที่เชื่อมกับ Thursday แทนได้",
        )
    return AVAILABLE


def _needs_voice(container: Any) -> Unavailable:
    voice = getattr(container, "voice", None)
    if voice is None:
        return Unavailable(state=Availability.UNAVAILABLE, reason="ยังไม่ได้เปิดใช้เสียงบนเครื่องนี้")
    return AVAILABLE


def _needs_gesture(container: Any) -> Unavailable:
    """§52's own example: "Gesture Control ยังไม่ได้เปิดใช้บนเครื่องนี้"."""
    if getattr(container, "gesture_mode", None) is None:
        return Unavailable(
            state=Availability.UNAVAILABLE, reason="ยังไม่ได้เปิดใช้การสั่งงานด้วยท่าทางบนเครื่องนี้"
        )
    return _needs_camera(container)


def _needs_second_device(container: Any) -> Unavailable:
    hub = getattr(container, "hub", None)
    if hub is not None and len(hub.online()) >= 2:
        return AVAILABLE
    return Unavailable(
        state=Availability.NEEDS_DEVICE,
        reason="ตอนนี้มีเครื่องที่เชื่อมต่ออยู่เครื่องเดียว",
        alternative="เชื่อมอีกเครื่อง เช่น มือถือหรือคอมอีกตัว แล้วสั่งข้ามเครื่องได้",
    )


def _any_device_supports(container: Any, capability: str) -> bool:
    """Whether any attached node advertises this. The same `supports()` the hub enforces
    with, walked over the same sessions — so the tutor cannot believe in a capability the
    hub would refuse."""
    hub = getattr(container, "hub", None)
    if hub is None:
        return False
    for summary in hub.online():
        caps = getattr(summary, "capabilities", None)
        if caps is not None and caps.supports(capability):
            return True
    return False


# ------------------------------------------------------------------------- the catalogue


FEATURES: tuple[Feature, ...] = (
    Feature(
        key="conversation",
        area=Area.BASICS,
        title="พูดคุยกับ Thursday",
        summary="บอกสิ่งที่ต้องการด้วยภาษาปกติ ไม่ต้องจำคำสั่ง",
        examples=("ช่วยสรุปเรื่องนี้ให้หน่อย", "ตอนนี้กี่โมงแล้ว"),
        probe=_always,
        depth=1,
    ),
    Feature(
        key="stop_everything",
        area=Area.SAFETY,
        title="สั่งให้หยุดทุกอย่าง",
        summary="พูดว่า “Thursday หยุดทั้งหมด” เมื่อไหร่ก็ได้ ผมจะหยุดทันที",
        examples=("Thursday หยุดทั้งหมด", "หยุด"),
        probe=_always,
        # Taught early on purpose. §56 names it as one of the first lessons, and knowing how
        # to stop something is what makes it safe to try anything else.
        depth=1,
    ),
    Feature(
        key="open_app",
        area=Area.COMPUTER,
        title="เปิดโปรแกรม",
        summary="สั่งเปิดโปรแกรมบนเครื่องที่เชื่อมต่ออยู่",
        examples=("เปิด Chrome", "เข้า Excel ให้หน่อย"),
        probe=_needs_device,
        depth=1,
    ),
    Feature(
        key="file_search",
        area=Area.FILES,
        title="ค้นไฟล์",
        summary="หาไฟล์จากคำอธิบาย ไม่ต้องรู้ว่าอยู่โฟลเดอร์ไหน",
        examples=("หาไฟล์คะแนนล่าสุด", "ไฟล์ Excel ที่แก้เมื่อวาน"),
        probe=_needs_device,
        depth=2,
    ),
    Feature(
        key="screen_context",
        area=Area.COMPUTER,
        title="ถามเกี่ยวกับหน้าจอ",
        summary="ถามได้ว่าตอนนี้เปิดอะไรอยู่ หรือให้ช่วยอ่านสิ่งที่เห็นบนจอ",
        examples=("ตอนนี้ฉันเปิดอะไรอยู่", "อ่านหน้านี้ให้หน่อย"),
        probe=_needs_device,
        depth=2,
    ),
    Feature(
        key="memory",
        area=Area.MEMORY,
        title="จำสิ่งสำคัญ",
        summary="บอกให้ผมจำไว้ แล้วผมจะใช้มันในงานครั้งต่อไป",
        examples=("Thursday จำไว้ว่าฉันชอบรายงานแบบสั้น", "นายจำอะไรเกี่ยวกับฉันบ้าง"),
        probe=_always,
        depth=3,
    ),
    Feature(
        key="voice",
        area=Area.BASICS,
        title="สั่งงานด้วยเสียง",
        summary="เรียกชื่อผมแล้วพูดได้เลย ไม่ต้องพิมพ์",
        examples=("Thursday เปิด Chrome",),
        probe=_needs_voice,
        depth=2,
    ),
    Feature(
        key="multi_step",
        area=Area.AGENTS,
        title="งานหลายขั้นตอน",
        summary="บอกผลลัพธ์ที่ต้องการ ผมแบ่งงานและตรวจผลให้เอง",
        examples=("วิเคราะห์ไฟล์นี้ ทำกราฟ แล้วเขียนรายงาน",),
        probe=_always,
        depth=4,
    ),
    Feature(
        key="automation",
        area=Area.AUTOMATION,
        title="ทำงานอัตโนมัติ",
        summary="งานที่ทำซ้ำ ๆ ให้ผมทำเองตามเวลาหรือตามเงื่อนไขได้",
        examples=("ทำแบบนี้ทุกเช้า",),
        probe=_always,
        depth=5,
        safety_notes="ผมจะสรุปให้ก่อนเสมอว่าจะทำอะไร อันไหนทำเอง อันไหนต้องถามคุณก่อน",
    ),
    Feature(
        key="skills",
        area=Area.AUTOMATION,
        title="จำวิธีทำงานเป็นขั้นตอน",
        summary="งานที่ทำบ่อย ผมบันทึกเป็นขั้นตอนไว้ แล้วเรียกใช้ด้วยประโยคเดียว",
        examples=("ทำรายงานคะแนนแบบเดิม",),
        probe=_always,
        depth=5,
    ),
    Feature(
        key="vision",
        area=Area.SEEING,
        title="ใช้กล้องช่วยดู",
        summary="ยกเอกสารขึ้นหน้ากล้องแล้วให้ผมอ่านหรืออธิบายให้",
        examples=("Thursday อ่านอันนี้ให้หน่อย",),
        probe=_needs_camera,
        depth=6,
        safety_notes="กล้องปิดอยู่เสมอจนกว่าคุณจะเปิด และจะมีสัญญาณแสดงตลอดเวลาที่กล้องทำงาน",
    ),
    Feature(
        key="gesture",
        area=Area.SEEING,
        title="สั่งงานด้วยท่าทาง",
        summary="ชี้ จีบนิ้ว หรือปัดมือ เพื่อสั่งงานบางอย่างโดยไม่ต้องพูด",
        examples=(),
        probe=_needs_gesture,
        depth=6,
        safety_notes="ท่าทางอย่างเดียวยืนยันการลบ การจ่ายเงิน หรือการส่งข้อมูลออกนอกเครื่องไม่ได้",
    ),
    Feature(
        key="multi_device",
        area=Area.DEVICES,
        title="สั่งข้ามเครื่อง",
        summary="สั่งจากมือถือให้ผมทำงานบนคอม หรือย้ายงานระหว่างเครื่อง",
        examples=("Thursday เปิด Chrome ที่คอมบ้าน",),
        probe=_needs_second_device,
        depth=7,
    ),
    Feature(
        key="permissions",
        area=Area.SAFETY,
        title="สิ่งที่ผมต้องขออนุญาตก่อน",
        summary="งานที่ลบของ ส่งข้อมูลออกนอกเครื่อง หรือเปลี่ยนระบบ ผมจะถามคุณก่อนเสมอ",
        examples=("ทำไมเมื่อกี้ถึงถามฉันก่อน",),
        probe=_always,
        depth=3,
    ),
)

FEATURES_BY_KEY: dict[str, Feature] = {f.key: f for f in FEATURES}


# ------------------------------------------------------------------------------ reading


@dataclass(frozen=True)
class FeatureStatus:
    """A feature as the owner meets it: what it is, and whether they can use it here."""

    feature: Feature
    availability: Unavailable

    @property
    def usable(self) -> bool:
        return self.availability.state.usable

    def render(self) -> dict:
        body: dict[str, Any] = {
            "key": self.feature.key,
            "area": self.feature.area.value,
            "title": self.feature.title,
            "summary": self.feature.summary,
            "examples": list(self.feature.examples),
            "available": self.usable,
        }
        if self.feature.safety_notes:
            body["safety"] = self.feature.safety_notes
        if not self.usable:
            body["reason"] = self.availability.reason
            if self.availability.alternative:
                body["alternative"] = self.availability.alternative
        return body


def status(container: Any, key: str) -> FeatureStatus | None:
    feature = FEATURES_BY_KEY.get(key)
    if feature is None:
        return None
    return FeatureStatus(feature=feature, availability=feature.availability(container))


def catalogue(container: Any, *, usable_only: bool = False) -> list[FeatureStatus]:
    """Every declared feature, each checked against this machine now."""
    rows = [FeatureStatus(feature=f, availability=f.availability(container)) for f in FEATURES]
    return [row for row in rows if row.usable] if usable_only else rows


def from_agents(container: Any) -> list[dict]:
    """Features an agent describes for itself (§61).

    This is the extension point the spec asks for: a new agent that writes one sentence for
    a person is taught without anybody editing this file. One that does not is **skipped** —
    never described by `AgentSpec.description`, which is written for whoever wired it.
    """
    registry = getattr(container, "agents", None)
    if registry is None:
        return []
    described = []
    for spec in registry.specs():
        if not spec.user_description:
            continue
        described.append(
            {
                "name": spec.name,
                "summary": spec.user_description,
                "examples": list(spec.user_examples),
                "safety": spec.safety_notes,
                "requirements": list(spec.requirements),
            }
        )
    return described


def areas(container: Any) -> list[dict]:
    """§33. What Thursday can do here, grouped, with nothing to scroll.

    Areas with nothing usable in them are dropped rather than listed as unavailable: the
    answer to "what can you do" is what Thursday *can* do. What it cannot, and why, is a
    question the owner can then ask about one thing (§11), which is where §12's alternative
    belongs — attached to the feature they asked about, not scattered through a summary.
    """
    grouped: dict[Area, list[FeatureStatus]] = {}
    for row in catalogue(container, usable_only=True):
        grouped.setdefault(row.feature.area, []).append(row)

    out = []
    for area in Area:  # declared order, so the answer reads the same way twice
        rows = grouped.get(area)
        if not rows:
            continue
        rows.sort(key=lambda r: r.feature.depth)
        out.append(
            {
                "area": area.value,
                "title": AREA_TITLES[area],
                "features": [r.feature.title for r in rows],
                "example": next((e for r in rows for e in r.feature.examples), ""),
            }
        )
    return out


def summary_line(container: Any) -> str:
    """The opening sentence of §33's answer. Counts areas, never features."""
    count = len(areas(container))
    if not count:
        return "ตอนนี้ผมยังช่วยอะไรไม่ได้ เพราะยังไม่ได้เชื่อมต่อกับเครื่องไหนเลยครับ"
    return f"ตอนนี้ผมช่วยคุณได้หลัก ๆ {count} ด้านครับ"


def unavailable_reason(container: Any, key: str) -> str:
    """§12 in one string: why not, and what to do instead."""
    row = status(container, key)
    if row is None or row.usable:
        return ""
    reason = row.availability.reason
    if row.availability.alternative:
        return f"{reason} — {row.availability.alternative}"
    return reason
