"""Library Agent (§15, V13).

The brief lists library activities, usage statistics, project reporting, collection tasks,
publicity and event planning. This agent does the two that are **counted rather than
written**: what moved off the shelves, and what is on them.

Those two carry the deadlines. A stock report and a term's circulation figures are asked for
by somebody above the librarian, with a date attached, and they are read as fact. That makes
them exactly the wrong place for a plausible number — so the counting happens in
`thursday_school`, over rows the report can point at, and the figures go up to the Supervisor
as `percentages` and `count` for it to recompute (§18).

Three distinctions it keeps that are easy to collapse, and each of which changes a number
somebody acts on:

* **Overdue is not the same as returned late.** One is a list to chase; the other is a term
  reviewed. Conflating them under- or over-counts, depending which way you lean.
* **Per-pupil needs the roll.** Given no enrolment figure the ratio is omitted rather than
  computed over the children who happened to borrow — which flatters a library exactly in
  proportion to how few used it.
* **An unrecorded publication year is not a young book.** Age shares are taken over the
  dated stock, and the undated count is reported beside them.

It does not decide. It will say that 62% of the science holdings predate the cut-off; it
will not say the collection is bad. Weeding is a librarian's call and a threshold invented
here would be a standard this project made up.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from thursday_school.circulation import CirculationError, summarise
from thursday_school.circulation import build as build_loans
from thursday_school.collection import CollectionError, analyse, gaps
from thursday_school.collection import build as build_items
from thursday_shared.enums import ModelTier, PermissionLevel
from thursday_shared.models import AgentResult, AgentSpec, Budget, JobContract

from thursday_agents.base import BaseAgent

ACTIONS: tuple[str, ...] = ("circulation", "collection", "gaps")


class LibraryAgent(BaseAgent):
    spec = AgentSpec(
        name="library",
        description=(
            "Counts library circulation and holdings: loans by class and month, overdue "
            "lists, collection age and category shares, and the gap against a target "
            "distribution. Computes every figure from the records."
        ),
        capabilities=[
            "library",
            "circulation",
            "loans",
            "collection",
            "holdings",
            "statistics",
            "school",
            "report",
        ],
        tools=[],
        agent_type="specialist",
        supported_input=["action", "loans", "items", "asof", "enrolled", "target"],
        supported_output=["report", "summary"],
        output_schema={"report": "dict", "summary": "string", "action": "string"},
        permission_ceiling=PermissionLevel.READ,
        default_budget=Budget(seconds=30, tool_calls=0, usd=0.0),
        model_tier=ModelTier.LOCAL,
        cost_profile="free",
        latency_profile="instant",
        # Borrowing records name children. They are counted here and go nowhere.
        privacy_profile="local_only",
        user_description=(
            "สรุปสถิติห้องสมุดจากข้อมูลจริง — ยืมคืนรายชั้น รายเดือน รายการค้างส่ง "
            "และวิเคราะห์หนังสือในคอลเลกชันว่ามีอะไรและเก่าแค่ไหน"
        ),
        user_examples=[
            "สรุปสถิติการยืมภาคเรียนนี้",
            "มีหนังสือค้างส่งกี่เล่ม ใครบ้าง",
            "วิเคราะห์หนังสือในห้องสมุด เก่ากว่าปี 2555 กี่เปอร์เซ็นต์",
            "เทียบสัดส่วนหมวดหนังสือกับเป้าหมาย",
        ],
        safety_notes=(
            "นับจากระเบียนที่ให้มาเท่านั้น ไม่ประมาณค่า — ถ้าไม่ได้บอกจำนวนนักเรียน "
            "จะไม่คิดค่าเฉลี่ยต่อคนให้ และจะไม่ตัดสินว่าคอลเลกชันดีหรือไม่ดี"
        ),
        system_prompt="",
    )

    async def execute(self, contract: JobContract, ctx: Any) -> AgentResult:
        action = str(contract.inputs.get("action") or "").strip().lower()
        if action not in ACTIONS:
            return self._refuse(action, f"ไม่รู้จักงาน {action!r} — ทำได้: " + ", ".join(ACTIONS))

        try:
            report, summary = self._make(action, contract.inputs)
        except (CirculationError, CollectionError) as exc:
            return self._refuse(action, str(exc))
        except (KeyError, TypeError, ValueError) as exc:
            return self._refuse(action, f"ข้อมูลไม่ครบหรือผิดรูปแบบ: {exc}")

        return AgentResult(
            agent=self.spec.name,
            ok=True,
            output={"report": report, "summary": summary, "action": action, **_evidence(report)},
            summary=summary,
            evidence=[{"action": action, "counted": "นับจากระเบียนที่ให้มา"}],
        )

    def _make(self, action: str, inputs: dict[str, Any]) -> tuple[dict[str, Any], str]:
        if action == "circulation":
            loans = build_loans(list(inputs.get("loans") or []))
            report = summarise(
                loans,
                asof=_asof(inputs.get("asof")),
                enrolled=_optional_int(inputs.get("enrolled")),
                top=int(inputs.get("top", 5)),
            )
            summary = (
                f"ยืม {report['count']} ครั้ง จากผู้ยืม {report['borrowers']} คน "
                f"({report['titles']} ชื่อเรื่อง) — ยังไม่คืน {report['still_out']} "
                f"เกินกำหนด {report['overdue']} คืนช้า {report['returned_late']}"
            )
            if "loans_per_pupil" in report:
                summary += f" — เฉลี่ย {report['loans_per_pupil']} ครั้ง/คน"
            return report, summary

        items = build_items(list(inputs.get("items") or []))

        if action == "gaps":
            target = {str(k): float(v) for k, v in (inputs.get("target") or {}).items()}
            rows = gaps(items, target)
            report = {"rows": rows, "count": len(rows), "target": target}
            short = [r for r in rows if r["gap"] < 0]
            summary = f"เทียบ {len(rows)} หมวดกับเป้าหมาย — ต่ำกว่าเป้า {len(short)} หมวด" + (
                ": "
                + ", ".join(
                    f"{r['category']} ขาด {abs(r['copies_to_target']):g} เล่ม" for r in short[:3]
                )
                if short
                else ""
            )
            return report, summary

        report = analyse(
            items,
            older_than=_optional_int(inputs.get("older_than")),
            enrolled=_optional_int(inputs.get("enrolled")),
        )
        summary = f"{report['titles']} ชื่อเรื่อง {report['copies']} เล่ม"
        if "copies_per_pupil" in report:
            summary += f" — {report['copies_per_pupil']} เล่ม/คน"
        if "age" in report:
            age = report["age"]
            summary += (
                f" — เก่ากว่าปี {age['before']} อยู่ {age['older_share']}% "
                f"ของ {age['dated_copies']} เล่มที่ระบุปี"
            )
        if report["unknown_year"]:
            summary += f" (ไม่ระบุปี {report['unknown_year']} เล่ม)"
        return report, summary

    def _refuse(self, action: str, reason: str) -> AgentResult:
        return AgentResult(
            agent=self.spec.name,
            ok=False,
            output={"report": {}, "summary": "", "action": action},
            error=reason,
            summary=reason,
        )


def _evidence(report: dict[str, Any]) -> dict[str, Any]:
    """Lift what `Supervisor._check_arithmetic` recomputes to the top level (§18)."""
    return {k: report[k] for k in ("percentages", "count", "rows") if k in report}


def _asof(value: Any) -> date:
    """The day the question is about. Defaults to today, and is always stated in the output
    — "how many are overdue" has no answer without saying when."""
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(str(value))
    return datetime.now(UTC).date()


def _optional_int(value: Any) -> int | None:
    return None if value in (None, "") else int(value)
