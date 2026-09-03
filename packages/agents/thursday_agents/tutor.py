"""ThursdayTutor (§46–§48).

An agent that teaches, and the narrowest one in the bench. Its job is to pick a lesson,
explain a capability, run a safe demonstration, and record what happened (§46) — and §48
lists what it must never do: send email, delete files, purchase, change admin settings,
install software, alter permissions.

**That list is not enforced here.** It is enforced by the agent having a READ ceiling and no
tools, so there is no path from this class to any of them. Writing the prohibitions into a
check inside the tutor would be a second permission system — one that has to be kept in
agreement with the real one, and whose disagreements would be discovered by an agent doing
something nobody sanctioned. The Permission Engine already refuses these for every agent;
what this agent adds is that it cannot even ask.

So the interesting properties are all absences:

    tools=[]                       nothing to call
    permission_ceiling=READ        below every action on §48's list
    capabilities=tutorial.*        a namespace with no executor behind it

and the one thing it *can* reach — Practice Mode — is a module with no execution path in it
(see `thursday_core.practice`), so a demonstration cannot become a performance.

The tutor also holds the line §67 draws: it may describe what Thursday can do, never what
Thursday knows. Memory contents, credentials, prompts and internal reasoning are not teaching
material, and the way to guarantee that is to give it nothing to read them with.
"""

from __future__ import annotations

from typing import Any

from thursday_core.catalogue import FEATURES_BY_KEY, areas, from_agents, status, summary_line
from thursday_core.lessons import LESSONS_BY_ID, next_lesson, path
from thursday_core.logging import get_logger
from thursday_shared.enums import ModelTier, PermissionLevel
from thursday_shared.models import AgentResult, AgentSpec, Budget, JobContract

from thursday_agents.base import BaseAgent

log = get_logger(__name__)

#: §47, verbatim. A namespace rather than a list of verbs, so it resolves through the same
#: prefix walk every other capability does (ADR 0007) — and `tutorial.*` has no executor
#: behind it at all, which is what makes the ceiling real rather than declared.
TUTOR_CAPABILITIES: tuple[str, ...] = (
    "tutorial.read",
    "tutorial.start",
    "tutorial.advance",
    "tutorial.skip",
    "tutorial.complete",
    "ui.highlight",
    "demo.safe",
    "capability.explain",
)

#: §48. Kept as data so a test can assert none of them is reachable, and *not* consulted at
#: runtime — a check here would be a second permission system to keep in agreement with the
#: real one. The Permission Engine refuses these for every agent; this agent has no tools to
#: attempt them with.
NEVER: tuple[str, ...] = (
    "email.send",
    "message.send",
    "file.delete",
    "purchase.make",
    "system.setting.write",
    "software.install",
    "permission.policy.modify",
    "permission.self_grant",
)


class TutorAgent(BaseAgent):
    """Teaches Thursday, using Thursday's own registries as its only source of truth."""

    spec = AgentSpec(
        name="tutor",
        description="Selects lessons, explains capabilities and runs safe demonstrations.",
        user_description="สอนวิธีใช้ผมทีละอย่าง ตามงานที่คุณใช้จริง",
        user_examples=["สอนอะไรใหม่ให้ฉันหน่อย", "ตอนนี้นายทำอะไรได้บ้าง", "Agent คืออะไร"],
        safety_notes="ผมสอนได้อย่างเดียว สั่งงานแทนคุณไม่ได้ และไม่แตะการตั้งค่าความปลอดภัย",
        capabilities=list(TUTOR_CAPABILITIES),
        # Nothing. Not "only safe tools" — none. A tool list is a thing that grows in a
        # hurry when somebody needs the tutor to demonstrate one more thing.
        tools=[],
        agent_type="specialist",
        supported_input=["question", "topic", "lesson_id"],
        supported_output=["explanation", "lesson", "areas"],
        output_schema={"explanation": "string", "lesson": "object?", "areas": "list?"},
        # Below every action on §48's list. The ceiling is the enforcement.
        permission_ceiling=PermissionLevel.READ,
        default_budget=Budget(seconds=20, tool_calls=0, usd=0.01),
        model_tier=ModelTier.FAST,
        cost_profile="cheap",
        latency_profile="fast",
        # Teaching is about the owner's own machine and their own progress. There is no
        # reason for any of it to leave, and LOCAL_ONLY says so rather than trusting that
        # nothing sensitive will ever end up in a lesson prompt.
        privacy_profile="local_only",
        requirements=[],
        system_prompt=(
            "You explain what Thursday can do, in the owner's language, one thing at a "
            "time. You never claim a capability the catalogue you were given does not "
            "list, and you never describe what Thursday remembers about the owner."
        ),
    )

    def __init__(self, container: Any) -> None:
        super().__init__()
        # The container, for reading registries. The tutor's honesty depends on reading the
        # same catalogue and lesson set everything else does (§52) rather than a copy.
        self._container = container

    async def execute(self, contract: JobContract, ctx: Any) -> AgentResult:
        topic = str(contract.inputs.get("topic", "")).strip()
        lesson_id = str(contract.inputs.get("lesson_id", "")).strip()
        question = str(contract.inputs.get("question", "")).strip()

        if lesson_id:
            return self._describe_lesson(lesson_id)
        if topic:
            return self._explain(topic)
        if question:
            return self._explain(question)
        return self._overview()

    # ------------------------------------------------------------------ §33 overview

    def _overview(self) -> AgentResult:
        """ "ตอนนี้นายทำอะไรได้บ้าง" — grouped, and short."""
        grouped = areas(self._container)
        return AgentResult(
            agent=self.spec.name,
            ok=True,
            output={
                "explanation": summary_line(self._container),
                "areas": grouped,
            },
            summary=summary_line(self._container),
            # Every claim here came from the live catalogue, which is what the Supervisor
            # would want to know if it asked where this answer came from.
            evidence=[{"source": "catalogue", "areas": len(grouped)}],
            confidence=1.0,
        )

    # ------------------------------------------------------------------ §11 explain one

    def _explain(self, topic: str) -> AgentResult:
        """Explain one capability, including honestly when it will not work here (§12)."""
        feature = _match(topic)
        if feature is None:
            offer = next_lesson(self._container, self._container.learning)
            text = "ผมยังไม่แน่ใจว่าคุณหมายถึงเรื่องไหนครับ"
            if offer is not None:
                text += f" ลองเริ่มจาก “{offer.lesson.name}” ไหมครับ?"
            return AgentResult(
                agent=self.spec.name,
                ok=True,
                output={"explanation": text},
                summary=text,
                confidence=0.4,
            )

        row = status(self._container, feature.key)
        parts = [feature.summary]
        if feature.examples:
            parts.append(f"เช่น พูดว่า “{feature.examples[0]}”")
        if row is not None and not row.usable:
            # §11: never recommend a feature the machine cannot run without saying so.
            parts.append(row.availability.reason)
            if row.availability.alternative:
                parts.append(row.availability.alternative)
        if feature.safety_notes:
            parts.append(feature.safety_notes)

        text = " ".join(parts)
        return AgentResult(
            agent=self.spec.name,
            ok=True,
            output={
                "explanation": text,
                "feature": feature.key,
                "available": bool(row and row.usable),
            },
            summary=text,
            evidence=[{"source": "catalogue", "feature": feature.key}],
            confidence=0.9,
        )

    # ------------------------------------------------------------------ §32 next lesson

    def _describe_lesson(self, lesson_id: str) -> AgentResult:
        lesson = LESSONS_BY_ID.get(lesson_id)
        if lesson is None:
            text = "ไม่พบบทเรียนนี้ครับ"
            return AgentResult(
                agent=self.spec.name, ok=False, output={"explanation": text}, error=text
            )
        first = lesson.steps[0]
        return AgentResult(
            agent=self.spec.name,
            ok=True,
            output={
                "explanation": first.show,
                "lesson": {
                    "id": lesson.id,
                    "name": lesson.name,
                    "try": first.try_this,
                    "steps": len(lesson.steps),
                },
            },
            summary=first.show,
            confidence=1.0,
        )

    # ------------------------------------------------------------------ used by the API

    def suggest(self) -> dict | None:
        offer = next_lesson(self._container, self._container.learning)
        return offer.render() if offer else None

    def learning_path(self) -> list[dict]:
        return path(self._container, self._container.learning)

    def agent_descriptions(self) -> list[dict]:
        return from_agents(self._container)


#: Words the owner might use for each feature. An allowlist, like everything else that turns
#: what somebody said into what Thursday does — a fuzzy match over feature titles would let
#: "delete" find "file" and start explaining deletion to somebody who asked about something
#: else.
_TOPIC_WORDS: dict[str, tuple[str, ...]] = {
    "vision": ("กล้อง", "camera", "ภาพ", "vision", "ocr", "อ่านภาพ"),
    "gesture": ("ท่าทาง", "gesture", "มือ"),
    "voice": ("เสียง", "voice", "พูด", "ไมค์", "ไมโครโฟน"),
    "memory": ("จำ", "ความจำ", "memory", "จดจำ"),
    "multi_step": ("agent", "เอเจนต์", "หลายขั้นตอน", "ทีม", "งานใหญ่"),
    "automation": ("อัตโนมัติ", "automation", "ทุกเช้า", "ตั้งเวลา"),
    "skills": ("skill", "ทักษะ", "แบบเดิม"),
    "multi_device": ("หลายเครื่อง", "มือถือ", "ข้ามเครื่อง", "อีกเครื่อง"),
    "file_search": ("ไฟล์", "file", "ค้นไฟล์", "หาไฟล์", "เอกสาร"),
    "open_app": ("เปิดโปรแกรม", "โปรแกรม", "แอป", "app"),
    "screen_context": ("หน้าจอ", "screen", "จอ"),
    "permissions": ("อนุญาต", "permission", "ขออนุญาต", "ทำไมถึงถาม"),
    "stop_everything": ("หยุด", "stop"),
    "conversation": ("คุย", "พูดคุย", "ใช้ยังไง", "ใช้ไม่เป็น"),
}


def _match(topic: str):
    """Map what the owner said to a catalogue feature, or nothing.

    Nothing is the important branch. Guessing wrong means confidently explaining the wrong
    feature, which is worse than saying "ผมยังไม่แน่ใจ" and offering a starting point.
    """
    lowered = topic.lower()
    for key, words in _TOPIC_WORDS.items():
        if any(word in lowered for word in words):
            return FEATURES_BY_KEY.get(key)
    return None
