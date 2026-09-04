"""Plain language, and no stack traces in front of people (EASY INSTALL) — Sprint 65.

Three worked examples in the requirement, all the same move:

    ResearchAgent #84 running tool call 13   →  กำลังค้นข้อมูล
    Vision model inference node GPU-02       →  กำลังวิเคราะห์ภาพ
    SupervisorAgent verifying output         →  กำลังตรวจสอบผลลัพธ์

and one rule with no example, because there is nothing to show: *"Never show raw stack trace
to normal users."*

**This is an allowlist, not a filter.** The tempting version strips things — remove the agent
id, drop the traceback, hide the port. It fails the way Sprint 49's metrics fallback failed: a
filter removes what somebody thought of, and what nobody thought of is exactly what leaks. It
fails invisibly too, because a leaked internal reads as a slightly odd message rather than as
a bug. So phrases are declared in advance and anything unrecognised becomes deliberately vague.
"""

from __future__ import annotations

import pytest
from thursday_core.plain import (
    ACTIVITY_BY_CAPABILITY,
    UNKNOWN_FAILURE,
    WORKING,
    activity,
    friendly,
    leaks,
)
from thursday_shared.errors import DeviceActionRefused, ThursdayError

# --------------------------------------------------------------------------- activity


def test_the_requirements_own_three_examples():
    assert activity(capabilities=["research.web"]) == "กำลังค้นข้อมูล"
    assert activity(capabilities=["vision.describe"]) == "กำลังวิเคราะห์ภาพ"
    assert activity(capabilities=["verify.output"]) == "กำลังตรวจสอบผลลัพธ์"


def test_an_unknown_capability_becomes_vague_rather_than_revealing():
    """The allowlist's failure mode, and the reason it is the right one. "กำลังทำงาน" is less
    informative and always true; the alternative is a class name in front of somebody who
    did not ask for one."""
    assert activity(capabilities=["quantum.entangle"]) == WORKING
    assert activity(capabilities=[]) == WORKING
    assert activity() == WORKING


def test_there_is_no_way_to_pass_an_agent_name_in():
    """`activity` takes capabilities, not an agent. Nothing about an agent's identity can
    reach the screen even by accident, because there is no parameter for it."""
    import inspect

    parameters = inspect.signature(activity).parameters
    assert "agent" not in parameters
    assert set(parameters) == {"capabilities", "fallback"}


def test_every_declared_phrase_is_in_the_owners_language():
    """A table that grew an English entry would leak on exactly one code path, months later."""
    for capability, phrase in ACTIVITY_BY_CAPABILITY.items():
        assert phrase.startswith("กำลัง"), f"{capability} is not a Thai activity phrase"
        assert not leaks(phrase), f"{capability}: {leaks(phrase)}"


# --------------------------------------------------------------------------- errors


def test_the_requirements_bad_example_becomes_its_good_one():
    """Bad:  ConnectionError ECONNREFUSED localhost:11434
    Good: "AI ภายในเครื่องไม่ตอบสนอง ผมสามารถลองซ่อมให้ได้" """
    result = friendly(ConnectionError("ECONNREFUSED localhost:11434"))

    assert "AI ภายในเครื่อง" in result.message
    assert result.repairable is True
    assert not leaks(result.message)


def test_the_bug_this_sprint_actually_found():
    """`agent.failed` carried `str(exc)` into a payload that reaches the UI. A missing Python
    package is not something a normal user can be shown."""
    result = friendly(ModuleNotFoundError("No module named 'redis'"))

    assert not leaks(result.message), leaks(result.message)
    assert result.repairable is True


@pytest.mark.parametrize(
    "exc",
    [
        ConnectionError("ECONNREFUSED localhost:11434"),
        ConnectionRefusedError("[Errno 111] Connection refused"),
        TimeoutError("timed out after 30s"),
        ModuleNotFoundError("No module named 'redis'"),
        OSError("[Errno 28] No space left on device"),
        RuntimeError('Traceback (most recent call last): File "/x.py", line 3'),
        ValueError("<object at 0x7f9c1a2b3d40> is not JSON serialisable"),
        Exception("sqlalchemy.exc.OperationalError: no such table: memories"),
    ],
)
def test_no_exception_leaks_anything_forbidden(exc):
    """The list is the requirement's own, plus the shapes a leaked internal takes. Every one
    of these is a real error this codebase can raise."""
    result = friendly(exc)
    assert not leaks(result.message), f"{exc!r} → {result.message!r} leaks {leaks(result.message)}"


def test_an_unrecognised_failure_says_what_it_can_and_no_more():
    result = friendly(RuntimeError("something nobody has seen before"))
    assert result.message == UNKNOWN_FAILURE
    assert result.repairable is True


def test_a_thursday_error_keeps_its_own_sentence():
    """These are already written for a person. Wrapping "Pixel is not trusted to control
    other machines" in a generic apology loses the one useful thing in it."""
    refusal = DeviceActionRefused("Pixel is not trusted to control other machines")
    result = friendly(refusal)

    assert result.message == "Pixel is not trusted to control other machines"
    assert result.repairable is False, "a refusal is not something Repair can fix"


def test_a_refusal_is_not_offered_a_repair_button():
    """A Repair button beside a problem it cannot fix teaches people the button does
    nothing — which is worse than not offering it."""
    assert friendly(ThursdayError("the daily cost cap is reached")).repairable is False


def test_the_original_is_kept_but_never_in_the_message():
    """Developer Options needs it; the message field must not carry it. Separate fields so
    no template can include it by accident."""
    result = friendly(ConnectionError("ECONNREFUSED localhost:11434"))

    assert "11434" in result.technical
    assert "11434" not in result.message


def test_content_beats_a_generic_type():
    """A ConnectionError naming port 11434 is more useful as "the local AI is not
    responding" than as "could not connect"."""
    generic = friendly(ConnectionError("connection reset by peer"))
    specific = friendly(ConnectionError("cannot reach http://127.0.0.1:11434/api/tags"))

    assert "AI ภายในเครื่อง" in specific.message
    assert specific.message != generic.message


# --------------------------------------------------------------------------- in the events


async def test_a_failing_agent_reports_an_activity_and_not_its_class_name(container):
    """The path the bug was on. `agent.failed` reaches the UI, and what it carries is what
    somebody sees."""
    from thursday_agents.base import BaseAgent
    from thursday_shared.models import AgentResult, AgentSpec

    seen: list[dict] = []

    class Ctx:
        from thursday_shared.models import Spend as _Spend

        spend = _Spend()

        async def emit(self, event):
            seen.append(dict(event.payload))

    class Broken(BaseAgent):
        spec = AgentSpec(
            name="ResearchAgent",
            description="finds things",
            capabilities=["research.web"],
            output_schema={"type": "object"},
        )

        async def execute(self, contract, ctx) -> AgentResult:
            raise ConnectionError("ECONNREFUSED localhost:11434")

    await Broken().run(contract=None, ctx=Ctx())

    started, failed = seen[0], seen[-1]
    assert started["activity"] == "กำลังค้นข้อมูล"
    assert not leaks(failed["error"]), leaks(failed["error"])
    assert failed["repairable"] is True
    # And the internal name survives for world state and the operator's log.
    assert failed["agent"] == "ResearchAgent"


def test_world_state_still_tracks_agents_by_name():
    """The regression this sprint nearly introduced.

    Hiding the agent name from the UI by removing it from the payload would also have
    stopped `WorldStateProjector.on_agent` populating `running_agents` — it keys on exactly that
    field and returns early without it. So `agent` stayed for internal consumers and
    `activity` was added for display, and this asserts both halves are still there.
    """
    import inspect

    from thursday_agents.base import BaseAgent
    from thursday_core.world import WorldStateProjector

    emitted = inspect.getsource(BaseAgent.run)
    assert '"agent": self.spec.name' in emitted, "world state's key left the payload"
    assert '"activity": activity(' in emitted, "the display string left the payload"

    consumer = inspect.getsource(WorldStateProjector.on_agent)
    assert 'payload.get("agent"' in consumer, "the tracker no longer reads what is emitted"
