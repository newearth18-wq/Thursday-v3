"""Teacher Agent (§15, V13).

The brief lists lesson plans, worksheets, tests, answer keys, rubrics, teaching materials and
presentations. This agent does the part of that list which can be **checked**: the documents
whose correctness is arithmetic rather than opinion.

That is a narrower agent than the list implies, and deliberately so. A rubric, an exam
blueprint and a lesson plan all have a number that must come out right — weights totalling
100%, cells matching the declared item count, activities fitting the period — and all three
are documents somebody else holds the teacher to. An inspector asks for the ตารางวิเคราะห์;
a parent asks why the mark was 17. A plausible figure that is wrong is the worst possible
output there, because it survives every check except the one nobody ran.

So the arithmetic is computed in `thursday_school`, never asked of a model, and it is handed
to the Supervisor as evidence rather than as a claim: `percentages` and `count` are the keys
`Supervisor._check_arithmetic` recomputes for itself (§18). An agent that says the weights
total 100% and ships the weights is an agent that can be caught being wrong.

**What it refuses.** A rubric whose weights miss 100 is not rescaled, a lesson that overruns
its period is not trimmed, and a blueprint that contradicts its own totals is not reconciled.
Each comes back as a refusal naming the numbers, because which criterion to reweight and
which activity to shorten is teaching, and the person who has to defend the document is the
person who should decide.

It writes no files and needs no tools: it returns structured documents, and saving one is
`file.write` through the ordinary permission path like anything else.
"""

from __future__ import annotations

from typing import Any

from thursday_school.blueprint import BlueprintError
from thursday_school.blueprint import build as build_blueprint
from thursday_school.lesson import LessonError
from thursday_school.lesson import build as build_lesson
from thursday_school.rubric import DEFAULT_BANDS, RubricError
from thursday_school.rubric import build as build_rubric
from thursday_shared.enums import ModelTier, PermissionLevel
from thursday_shared.models import AgentResult, AgentSpec, Budget, JobContract

from thursday_agents.base import BaseAgent

#: What this agent will make. Named actions rather than a free-form instruction, because
#: each one has its own arithmetic and its own refusal.
ACTIONS: tuple[str, ...] = ("rubric", "blueprint", "lesson", "score")


class TeacherAgent(BaseAgent):
    spec = AgentSpec(
        name="teacher",
        description=(
            "Builds the teaching documents whose correctness is arithmetic: scoring rubrics, "
            "exam blueprints (ตารางวิเคราะห์ข้อสอบ) and timed lesson plans. Computes every "
            "figure and refuses a document that does not add up."
        ),
        capabilities=[
            "teaching",
            "rubric",
            "assessment",
            "exam",
            "blueprint",
            "lesson",
            "school",
            "grading",
        ],
        tools=[],
        agent_type="specialist",
        supported_input=["action", "title", "criteria", "rows", "activities"],
        supported_output=["document", "summary"],
        output_schema={"document": "dict", "summary": "string", "action": "string"},
        # It computes and returns. Writing the result to a file is `file.write` through the
        # ordinary path, by whoever asked for it.
        permission_ceiling=PermissionLevel.READ,
        default_budget=Budget(seconds=30, tool_calls=0, usd=0.0),
        model_tier=ModelTier.LOCAL,
        cost_profile="free",
        latency_profile="instant",
        # Arithmetic on the owner's own marks and lesson content. None of it leaves.
        privacy_profile="local_only",
        user_description=(
            "ช่วยทำเอกสารการสอนที่ต้องคำนวณให้ถูก — รูบริกให้คะแนน ตารางวิเคราะห์ข้อสอบ "
            "และแผนการสอนที่เวลาลงตัวกับคาบ"
        ),
        user_examples=[
            "ทำรูบริกประเมินโครงงาน 20 คะแนน",
            "ทำตารางวิเคราะห์ข้อสอบกลางภาค",
            "วางแผนการสอนคาบ 50 นาที เรื่องแรงเสียดทาน",
            "คิดคะแนนจากรูบริกนี้",
        ],
        safety_notes=(
            "คำนวณตัวเลขให้ทั้งหมดและปฏิเสธเอกสารที่ตัวเลขไม่ลงตัว แทนที่จะปรับให้เอง — "
            "การตัดสินใจว่าจะลดน้ำหนักเกณฑ์ไหนหรือตัดกิจกรรมใดเป็นเรื่องของครู"
        ),
        system_prompt="",
    )

    async def execute(self, contract: JobContract, ctx: Any) -> AgentResult:
        action = str(contract.inputs.get("action") or "").strip().lower()
        if action not in ACTIONS:
            return self._refuse(
                action,
                f"ไม่รู้จักงาน {action!r} — ทำได้: " + ", ".join(ACTIONS),
            )

        try:
            document, summary = self._make(action, contract.inputs)
        except (RubricError, BlueprintError, LessonError) as exc:
            # The refusal carries the arithmetic that stopped it, which is the part the
            # teacher can act on.
            return self._refuse(action, str(exc))
        except (KeyError, TypeError, ValueError) as exc:
            return self._refuse(action, f"ข้อมูลไม่ครบหรือผิดรูปแบบ: {exc}")

        return AgentResult(
            agent=self.spec.name,
            ok=True,
            output={
                "document": document,
                "summary": summary,
                "action": action,
                **_evidence(document),
            },
            summary=summary,
            evidence=[{"action": action, "checked": "ตัวเลขคำนวณและตรวจสอบแล้ว"}],
        )

    def _make(self, action: str, inputs: dict[str, Any]) -> tuple[dict[str, Any], str]:
        if action == "rubric":
            rubric = build_rubric(
                str(inputs.get("title") or "รูบริกประเมิน"),
                list(inputs.get("criteria") or []),
                bands=tuple(inputs.get("bands") or DEFAULT_BANDS),
                total_points=float(inputs.get("total_points", 100.0)),
            )
            document = rubric.to_dict()
            summary = (
                f"รูบริก {rubric.title}: {len(rubric.criteria)} เกณฑ์ "
                f"น้ำหนักรวม {rubric.weight_total:g}% เต็ม {rubric.total_points:g} คะแนน"
            )
            return document, summary

        if action == "score":
            rubric = build_rubric(
                str(inputs.get("title") or "รูบริกประเมิน"),
                list(inputs.get("criteria") or []),
                bands=tuple(inputs.get("bands") or DEFAULT_BANDS),
                total_points=float(inputs.get("total_points", 100.0)),
            )
            awarded = dict(inputs.get("awarded") or {})
            result = rubric.score(awarded)
            summary = (
                f"ได้ {result['earned']:g} จาก {result['total']:g} คะแนน ({result['percent']:g}%)"
            )
            return result, summary

        if action == "blueprint":
            blueprint = build_blueprint(
                str(inputs.get("title") or "ตารางวิเคราะห์ข้อสอบ"),
                list(inputs.get("rows") or []),
                expect_items=_optional_int(inputs.get("expect_items")),
                expect_marks=_optional_float(inputs.get("expect_marks")),
            )
            document = blueprint.to_dict()
            summary = (
                f"{blueprint.title}: {blueprint.total_items} ข้อ "
                f"{blueprint.total_marks:g} คะแนน "
                f"ขั้นวิเคราะห์ขึ้นไป {blueprint.higher_order_share():g}%"
            )
            return document, summary

        lesson = build_lesson(
            str(inputs.get("title") or inputs.get("topic") or ""),
            list(inputs.get("activities") or []),
            minutes=int(inputs.get("minutes", 50)),
            objectives=[str(o) for o in inputs.get("objectives") or []],
            assessment=str(inputs.get("assessment") or ""),
        )
        document = lesson.to_dict()
        missing = lesson.missing_phases()
        summary = (
            f"แผนการสอน {lesson.topic}: {lesson.planned} นาทีจาก {lesson.minutes} นาที "
            f"(เหลือ {lesson.slack} นาที)" + (f" — ยังไม่มี{', '.join(missing)}" if missing else "")
        )
        return document, summary

    def _refuse(self, action: str, reason: str) -> AgentResult:
        return AgentResult(
            agent=self.spec.name,
            ok=False,
            output={"document": {}, "summary": "", "action": action},
            error=reason,
            summary=reason,
        )


def _evidence(document: dict[str, Any]) -> dict[str, Any]:
    """Lift the figures the Supervisor recomputes for itself up to the top level (§18).

    `percentages` and `count` are the keys `_check_arithmetic` reads. Copying them out of the
    document is what turns "the weights total 100%" from a claim this agent makes into one
    something else can catch it getting wrong.
    """
    lifted: dict[str, Any] = {}
    for key in ("percentages", "count", "items", "rows"):
        if key in document:
            lifted[key] = document[key]
    return lifted


def _optional_int(value: Any) -> int | None:
    return None if value in (None, "") else int(value)


def _optional_float(value: Any) -> float | None:
    return None if value in (None, "") else float(value)
