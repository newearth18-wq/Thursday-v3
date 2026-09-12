"""Event Agent (§15, V13).

The brief lists timeline, run sheet, scripts, stage cue, assignments, checklist and
evaluation. This agent does the ones that are **clock and roster** rather than prose: the
run sheet, who is on for what, and what nobody has been given.

A run sheet fails by arithmetic. Items get durations, somebody types clock times beside them
by hand, and the two stop agreeing halfway through the afternoon — by which point the
closing ceremony is at 15:40 and the buses left at 15:30. So the times are computed from the
durations rather than written alongside them, and an event that must end by a fixed time is
checked against it.

The clash check is the part that earns its place. A teacher on for two consecutive items has
zero minutes to get from one to the other, and that is invisible in a list: the two rows are
far apart on the page and their times only collide once somebody works the clock out. On the
day it is the thing that goes wrong.

**Refused, not trimmed.** An overrunning event comes back with the overrun named. Every item
belongs to somebody who was asked to prepare it, and which one loses five minutes is the
organiser's decision — one made silently is discovered on stage.
"""

from __future__ import annotations

from typing import Any

from thursday_school.runsheet import RunSheetError
from thursday_school.runsheet import build as build_runsheet
from thursday_shared.enums import ModelTier, PermissionLevel
from thursday_shared.models import AgentResult, AgentSpec, Budget, JobContract

from thursday_agents.base import BaseAgent

ACTIONS: tuple[str, ...] = ("runsheet",)


class EventAgent(BaseAgent):
    spec = AgentSpec(
        name="event",
        description=(
            "Builds an event run sheet from durations: computes every clock time, checks it "
            "against a hard finish, finds people rostered onto back-to-back items, and names "
            "what nobody has been assigned."
        ),
        capabilities=[
            "event",
            "runsheet",
            "timeline",
            "schedule",
            "roster",
            "school",
            "ceremony",
        ],
        tools=[],
        agent_type="specialist",
        supported_input=["action", "title", "start", "items", "must_end_by"],
        supported_output=["runsheet", "summary"],
        output_schema={"runsheet": "dict", "summary": "string", "action": "string"},
        permission_ceiling=PermissionLevel.READ,
        default_budget=Budget(seconds=30, tool_calls=0, usd=0.0),
        model_tier=ModelTier.LOCAL,
        cost_profile="free",
        latency_profile="instant",
        privacy_profile="local_only",
        user_description=(
            "ทำกำหนดการงานให้เวลาลงตัว — คิดเวลาเริ่ม-จบของทุกรายการให้เอง "
            "เช็คว่าจบทันเวลาไหม และเตือนถ้ามีคนต้องอยู่สองรายการติดกัน"
        ),
        user_examples=[
            "ทำกำหนดการงานเปิดบ้านวิชาการ เริ่มแปดโมง ต้องจบก่อนสิบเอ็ด",
            "ใครต้องอยู่สองรายการติดกันบ้าง",
            "กำหนดการนี้จบกี่โมง",
        ],
        safety_notes=(
            "คิดเวลาจากระยะเวลาที่ให้มาเท่านั้น ถ้าเกินเวลาที่ต้องจบจะบอกว่าเกินกี่นาที "
            "แต่ไม่ตัดรายการให้เอง เพราะทุกรายการมีคนเตรียมไว้แล้ว"
        ),
        system_prompt="",
    )

    async def execute(self, contract: JobContract, ctx: Any) -> AgentResult:
        inputs = contract.inputs
        action = str(inputs.get("action") or "runsheet").strip().lower()
        if action not in ACTIONS:
            return self._refuse(action, f"ไม่รู้จักงาน {action!r} — ทำได้: " + ", ".join(ACTIONS))

        try:
            sheet = build_runsheet(
                str(inputs.get("title") or ""),
                str(inputs.get("start") or "08:00"),
                list(inputs.get("items") or []),
                must_end_by=inputs.get("must_end_by") or None,
            )
        except RunSheetError as exc:
            return self._refuse(action, str(exc))
        except (KeyError, TypeError, ValueError) as exc:
            return self._refuse(action, f"ข้อมูลไม่ครบหรือผิดรูปแบบ: {exc}")

        document = sheet.to_dict()
        summary = (
            f"{sheet.title}: {sheet.start:%H:%M}–{sheet.end:%H:%M} "
            f"({sheet.minutes} นาที, {len(sheet.items)} รายการ)"
        )
        if document["clashes"]:
            names = ", ".join(sorted({c["owner"] for c in document["clashes"]}))
            summary += f" — {names} ต้องอยู่สองรายการติดกัน"
        if document["unassigned"]:
            summary += f" — ยังไม่มีผู้รับผิดชอบ {len(document['unassigned'])} รายการ"

        return AgentResult(
            agent=self.spec.name,
            ok=True,
            output={
                "runsheet": document,
                "summary": summary,
                "action": action,
                "items": document["items"],
                "count": document["count"],
            },
            summary=summary,
            evidence=[{"start": document["start"], "end": document["end"]}],
        )

    def _refuse(self, action: str, reason: str) -> AgentResult:
        return AgentResult(
            agent=self.spec.name,
            ok=False,
            output={"runsheet": {}, "summary": "", "action": action},
            error=reason,
            summary=reason,
        )
