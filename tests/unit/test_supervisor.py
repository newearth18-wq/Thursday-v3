"""Supervision — the component that gates the word 'success' (§18, §76)."""

from __future__ import annotations

import pytest
from thursday_core.supervisor import Supervisor
from thursday_shared.enums import AgentVerdict
from thursday_shared.ids import new_id
from thursday_shared.models import AgentResult, JobContract, ToolResult


def contract(**overrides) -> JobContract:
    base = {
        "task_id": new_id(),
        "step_id": new_id(),
        "agent": "computer",
        "objective": "open chrome",
        "output_schema": {"action": "string", "verified": "bool"},
        "success_criteria": ["output.verified is true"],
    }
    return JobContract(**{**base, **overrides})


@pytest.fixture
def supervisor() -> Supervisor:
    return Supervisor(models=None, use_llm_critique=False)


async def test_a_fully_verified_result_passes(supervisor):
    report = await supervisor.verify(
        contract(),
        AgentResult(
            agent="computer",
            ok=True,
            output={"action": "open_app", "verified": True},
            tool_results=[ToolResult(call_id=new_id(), tool="open_app", ok=True, verified=True)],
        ),
    )
    assert report.passed and report.verdict is AgentVerdict.PASS


async def test_a_dispatched_but_unobserved_action_never_passes(supervisor):
    """The heart of §76: the tool ran, nothing confirmed the effect."""
    report = await supervisor.verify(
        contract(),
        AgentResult(
            agent="computer",
            ok=True,
            output={"action": "open_app", "verified": False},
            tool_results=[ToolResult(call_id=new_id(), tool="open_app", ok=True, verified=False)],
        ),
    )
    assert not report.passed
    # Nothing more the agent can do about an unobservable effect — a human decides.
    assert report.verdict is AgentVerdict.ESCALATE
    assert "could not be confirmed" in report.critique


async def test_a_missing_output_field_is_a_retry(supervisor):
    report = await supervisor.verify(
        contract(), AgentResult(agent="computer", ok=True, output={"action": "x"})
    )
    assert report.verdict is AgentVerdict.RETRY
    assert "verified" in report.critique


async def test_percentages_that_do_not_total_are_caught(supervisor):
    report = await supervisor.verify(
        contract(output_schema={}, success_criteria=[]),
        AgentResult(agent="data", ok=True, output={"percentages": [40, 30, 10]}),
    )
    assert report.verdict is AgentVerdict.RETRY
    assert "sum to 80" in report.critique


async def test_a_declared_count_must_match_the_rows(supervisor):
    report = await supervisor.verify(
        contract(output_schema={}, success_criteria=[]),
        AgentResult(agent="data", ok=True, output={"count": 42, "items": [1, 2, 3]}),
    )
    assert report.verdict is AgentVerdict.RETRY
    assert "declared 42, found 3" in report.critique


async def test_research_output_without_sources_fails_provenance(supervisor):
    report = await supervisor.verify(
        contract(output_schema={"answer": "string"}, success_criteria=["claims name their source"]),
        AgentResult(agent="research", ok=True, output={"answer": "42", "sources": []}),
    )
    assert not report.passed
    assert "no sources" in report.critique


async def test_failures_a_repeat_cannot_fix_escalate_immediately(supervisor):
    report = await supervisor.verify(
        contract(),
        AgentResult(
            agent="computer", ok=False, error="FileNotFoundError: no executable for 'chrome'"
        ),
    )
    assert report.verdict is AgentVerdict.ESCALATE


async def test_transient_failures_are_retried_within_the_attempt_budget(supervisor):
    result = AgentResult(
        agent="computer", ok=False, error="TimeoutError: the device did not answer"
    )
    assert (
        await supervisor.verify(contract(), result, attempt=1, max_attempts=2)
    ).verdict is AgentVerdict.RETRY
    # Once the attempts are spent, it becomes the owner's problem rather than a loop (§96).
    assert (
        await supervisor.verify(contract(), result, attempt=2, max_attempts=2)
    ).verdict is AgentVerdict.ESCALATE


async def test_the_offline_verifier_escalates_rather_than_fabricating_a_pass():
    """A verifier with no reasoning model must not invent approval."""
    from thursday_core.model_router import ModelRouter
    from thursday_models.llm import RuleBasedLLM
    from thursday_shared.enums import ModelTier

    router = ModelRouter()
    router.register(ModelTier.LOCAL, RuleBasedLLM())
    router.register(ModelTier.REASONING, RuleBasedLLM())

    report = await Supervisor(router, use_llm_critique=True).verify(
        contract(output_schema={}, success_criteria=["the tone matches the owner's style"]),
        AgentResult(agent="document", ok=True, output={"document": "..."}),
    )
    assert not report.passed
