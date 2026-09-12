"""The Library agent, and the Supervisor checking its counts (V13)."""

from __future__ import annotations

import pytest
from thursday_agents.library import ACTIONS, LibraryAgent
from thursday_core.supervisor import Supervisor
from thursday_shared.enums import PermissionLevel
from thursday_shared.ids import new_id
from thursday_shared.models import JobContract, Spend

LOANS = [
    {
        "item": "ดาราศาสตร์",
        "borrower": "ก",
        "group": "ม.1/1",
        "borrowed_on": "2026-06-02",
        "due_on": "2026-06-16",
        "returned_on": "2026-06-14",
    },
    {
        "item": "ดาราศาสตร์",
        "borrower": "ข",
        "group": "ม.1/2",
        "borrowed_on": "2026-05-10",
        "due_on": "2026-05-24",
    },
    {
        "item": "นิทานไทย",
        "borrower": "ค",
        "group": "ม.1/1",
        "borrowed_on": "2026-06-01",
        "due_on": "2026-06-15",
        "returned_on": "2026-06-21",
    },
]
ITEMS = [
    {"title": "ดาราศาสตร์", "category": "วิทยาศาสตร์", "year": 2545, "copies": 4},
    {"title": "นิทานไทย", "category": "วรรณกรรม", "year": 2560, "copies": 6},
    {"title": "แผนที่", "category": "สังคม", "copies": 3},
]


class Ctx:
    spend = Spend()

    async def emit(self, event):
        return None


def contract(**inputs) -> JobContract:
    return JobContract(
        task_id=new_id(),
        step_id=new_id(),
        agent="library",
        objective="งานห้องสมุด",
        inputs=inputs,
    )


@pytest.fixture
def agent() -> LibraryAgent:
    return LibraryAgent()


async def test_it_counts_a_term_of_borrowing(agent):
    result = await agent.run(
        contract(action="circulation", loans=LOANS, asof="2026-07-01", enrolled=120), Ctx()
    )
    assert result.ok, result.error
    report = result.output["report"]
    assert report["count"] == 3
    assert report["overdue"] == 1
    assert report["returned_late"] == 1
    assert report["loans_per_pupil"] == 0.03
    assert "เกินกำหนด 1" in result.summary and "คืนช้า 1" in result.summary


async def test_without_a_roll_the_summary_does_not_claim_a_per_pupil_figure(agent):
    result = await agent.run(contract(action="circulation", loans=LOANS, asof="2026-07-01"), Ctx())
    assert result.ok
    assert "ครั้ง/คน" not in result.summary
    assert "loans_per_pupil" not in result.output["report"]


async def test_it_analyses_the_collection_and_says_what_is_undated(agent):
    result = await agent.run(
        contract(action="collection", items=ITEMS, older_than=2555, enrolled=100), Ctx()
    )
    assert result.ok, result.error
    report = result.output["report"]
    assert report["copies"] == 13
    assert report["unknown_year"] == 3
    assert report["age"]["dated_copies"] == 10
    assert "ไม่ระบุปี 3 เล่ม" in result.summary


async def test_it_compares_holdings_against_a_target(agent):
    result = await agent.run(
        contract(action="gaps", items=ITEMS, target={"วิทยาศาสตร์": 60, "วรรณกรรม": 20, "สังคม": 20}),
        Ctx(),
    )
    assert result.ok, result.error
    science = next(r for r in result.output["report"]["rows"] if r["category"] == "วิทยาศาสตร์")
    assert science["gap"] == pytest.approx(-29.23, abs=0.01)
    assert "ต่ำกว่าเป้า" in result.summary


async def test_the_day_the_question_is_about_is_always_stated(agent):
    """ "How many are overdue" has no answer without saying when."""
    result = await agent.run(contract(action="circulation", loans=LOANS, asof="2026-06-01"), Ctx())
    assert result.output["report"]["asof"] == "2026-06-01"
    assert result.output["report"]["overdue"] == 1


async def test_a_default_asof_is_still_recorded(agent):
    result = await agent.run(contract(action="circulation", loans=LOANS), Ctx())
    assert result.ok
    assert result.output["report"]["asof"], "the date used must be in the output"


# ------------------------------------------------------------------------------ refusals


async def test_an_impossible_loan_is_refused_with_the_dates(agent):
    result = await agent.run(
        contract(
            action="circulation",
            loans=[
                {"item": "ก", "borrower": "1", "borrowed_on": "2026-06-10", "due_on": "2026-06-01"}
            ],
        ),
        Ctx(),
    )
    assert not result.ok
    assert "2026-06-01" in result.error and "2026-06-10" in result.error


async def test_a_target_that_does_not_total_a_hundred_is_refused(agent):
    result = await agent.run(contract(action="gaps", items=ITEMS, target={"วิทยาศาสตร์": 50}), Ctx())
    assert not result.ok
    assert "100%" in result.error


async def test_an_unknown_action_lists_the_ones_that_exist(agent):
    result = await agent.run(contract(action="ซื้อหนังสือให้หน่อย"), Ctx())
    assert not result.ok
    for action in ACTIONS:
        assert action in result.error


async def test_no_records_is_refused_rather_than_reported_as_zero(agent):
    """An empty report reads as "the library was not used", which is a different claim
    from "nobody gave me the records"."""
    result = await agent.run(contract(action="circulation", loans=[]), Ctx())
    assert not result.ok
    assert "ไม่มีรายการ" in result.error


# ------------------------------------------------------- the Supervisor checks the numbers


async def test_the_group_shares_are_offered_for_recomputation(agent):
    result = await agent.run(contract(action="circulation", loans=LOANS, asof="2026-07-01"), Ctx())
    report = await Supervisor(models=None, use_llm_critique=False).verify(
        contract(action="circulation"), result
    )
    assert "percentages_total" in {c["name"] for c in report.checks}
    assert report.verdict.value == "PASS", report.checks


async def test_the_collection_shares_are_offered_too(agent):
    result = await agent.run(contract(action="collection", items=ITEMS), Ctx())
    assert sum(result.output["percentages"]) == pytest.approx(100.0, abs=0.05)


# --------------------------------------------------------------------------------- wiring


def test_the_agent_is_registered_and_keeps_borrowing_records_local(container):
    """Loan rows name children. A READ ceiling and `local_only` are how that stays true
    without anything having to remember it."""
    spec = container.agents.get("library").spec
    assert spec.permission_ceiling is PermissionLevel.READ
    assert spec.tools == []
    assert spec.privacy_profile == "local_only"
    assert spec.user_description and spec.user_examples and spec.output_schema
