"""The Event agent: the clock computed, the clashes surfaced (V13)."""

from __future__ import annotations

import pytest
from thursday_agents.event import ACTIONS, EventAgent
from thursday_core.supervisor import Supervisor
from thursday_shared.enums import PermissionLevel
from thursday_shared.ids import new_id
from thursday_shared.models import JobContract, Spend

ITEMS = [
    {"name": "ลงทะเบียน", "minutes": 30, "owners": ["ครูเอ"]},
    {"name": "พิธีเปิด", "minutes": 20, "owners": ["ผอ.", "ครูบี"]},
    {"name": "การแสดง ม.1", "minutes": 15, "owners": ["ครูบี"]},
    {"name": "ชมนิทรรศการ", "minutes": 60, "owners": ["ครูเอ"]},
    {"name": "พิธีปิด", "minutes": 15, "owners": ["ผอ."]},
]


class Ctx:
    spend = Spend()

    async def emit(self, event):
        return None


def contract(**inputs) -> JobContract:
    return JobContract(
        task_id=new_id(),
        step_id=new_id(),
        agent="event",
        objective="งานโรงเรียน",
        inputs=inputs,
    )


@pytest.fixture
def agent() -> EventAgent:
    return EventAgent()


async def test_it_lays_the_event_on_a_clock(agent):
    result = await agent.run(
        contract(title="เปิดบ้านวิชาการ", start="08:00", items=ITEMS, must_end_by="11:00"), Ctx()
    )
    assert result.ok, result.error
    sheet = result.output["runsheet"]
    assert sheet["start"] == "08:00" and sheet["end"] == "10:20"
    assert sheet["minutes"] == 140
    assert sheet["fits"]
    assert sheet["items"][1]["start"] == "08:30"


async def test_the_clash_is_named_in_the_summary_where_it_will_be_read(agent):
    """A teacher with zero minutes between two items is the thing that goes wrong on the
    day, and a report that buries it in a field nobody opens has not helped."""
    result = await agent.run(contract(title="เปิดบ้าน", start="08:00", items=ITEMS), Ctx())
    assert result.ok
    assert "ครูบี" in result.summary
    assert "ติดกัน" in result.summary
    assert result.output["runsheet"]["clashes"][0]["at"] == "08:50"


async def test_unassigned_items_are_counted_in_the_summary(agent):
    result = await agent.run(
        contract(
            title="ยังไม่ครบ",
            start="08:00",
            items=[{"name": "ก", "minutes": 10}, {"name": "ข", "minutes": 10}],
        ),
        Ctx(),
    )
    assert result.ok
    assert "ยังไม่มีผู้รับผิดชอบ 2 รายการ" in result.summary


async def test_load_per_person_is_reported(agent):
    result = await agent.run(contract(title="เปิดบ้าน", start="08:00", items=ITEMS), Ctx())
    load = {row["owner"]: row["minutes"] for row in result.output["runsheet"]["load"]}
    assert load == {"ครูเอ": 90, "ครูบี": 35, "ผอ.": 35}


async def test_an_overrun_is_refused_rather_than_trimmed(agent):
    result = await agent.run(
        contract(
            title="เกิน",
            start="08:00",
            items=[{"name": "ก", "minutes": 200}],
            must_end_by="10:00",
        ),
        Ctx(),
    )
    assert not result.ok
    assert "11:20" in result.error and "80 นาที" in result.error
    assert result.output["runsheet"] == {}


async def test_an_unreadable_time_is_refused_with_the_format(agent):
    result = await agent.run(
        contract(title="ผิด", start="แปดโมง", items=[{"name": "ก", "minutes": 10}]), Ctx()
    )
    assert not result.ok
    assert "HH:MM" in result.error


async def test_an_event_with_no_items_is_refused(agent):
    result = await agent.run(contract(title="ว่าง", start="08:00", items=[]), Ctx())
    assert not result.ok
    assert "อย่างน้อยหนึ่งรายการ" in result.error


async def test_the_action_defaults_to_the_only_one_there_is(agent):
    result = await agent.run(
        contract(title="ไม่ระบุ action", start="08:00", items=[{"name": "ก", "minutes": 10}]),
        Ctx(),
    )
    assert result.ok
    assert result.output["action"] == "runsheet"
    assert ACTIONS == ("runsheet",)


async def test_the_supervisor_can_check_the_item_count(agent):
    result = await agent.run(contract(title="เปิดบ้าน", start="08:00", items=ITEMS), Ctx())
    report = await Supervisor(models=None, use_llm_critique=False).verify(
        contract(title="x"), result
    )
    names = {c["name"] for c in report.checks}
    assert "count_matches_items" in names
    assert report.verdict.value == "PASS", report.checks


def test_all_three_school_agents_are_registered_and_read_only(container):
    """Phase 9, which was empty."""
    for name in ("teacher", "library", "event"):
        spec = container.agents.get(name).spec
        assert spec.permission_ceiling is PermissionLevel.READ, name
        assert spec.tools == [], name
        assert spec.privacy_profile == "local_only", name
        assert spec.user_description and spec.user_examples, name
