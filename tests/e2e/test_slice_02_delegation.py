"""Slice 2 — delegation and supervision.

Thursday is one identity. Behind that identity, work goes to whichever agent is equipped
for it, and no agent's word is taken for the result: the supervisor checks the output
against the objective before the owner hears about it (PART 15).

What these tests hold to is that the *owner-facing* behaviour never leaks the machinery.
The owner asked one thing and gets one answer; which agent did it is an audit detail, not a
conversational one.
"""

from __future__ import annotations

from thursday_shared.enums import AgentVerdict, DataSensitivity, TaskState
from thursday_shared.ids import new_id
from thursday_shared.models import UserRequest

from tests.helpers import connect_failing_node


async def test_the_owner_talks_to_one_identity_however_many_agents_ran(
    container, office_pc, adapter, session_id
):
    response = await container.engine.handle_request(
        UserRequest(
            conversation_id=session_id,
            text="Thursday เปิด chrome",
            device_id=office_pc.device_id,
        )
    )

    # The reply is Thursday's. It does not hand the owner an agent's transcript.
    assert "computer" not in response.text.lower()
    assert "agent" not in response.text.lower()

    # The delegation is on the record even though it never reached the conversation.
    assert container.bus.history("agent.*")


async def test_an_agent_with_none_of_its_tools_available_is_not_elected(container):
    """Delegating to an agent that cannot act produces a task that fails halfway through.

    Better no candidate — and an honest "I can't" — than a confident selection that then
    cannot run a single step.
    """
    everything = container.tools.names()
    assert container.agents.select(capabilities=["research"], available_tools=everything)

    starved = container.agents.select(capabilities=["research"], available_tools=[])
    assert starved is None


async def test_secret_content_never_reaches_an_agent_that_can_leave_the_machine(container):
    """§35 — the exclusion is absolute, not a scoring penalty.

    A low score still wins an election it is the only entrant in, which is exactly how a
    password ends up in a web search.
    """
    ordinary = container.agents.select(
        capabilities=["research", "summarize"], sensitivity=DataSensitivity.INTERNAL
    )
    assert ordinary is not None and ordinary.spec.privacy_profile == "any"

    secret = container.agents.select(
        capabilities=["research", "summarize"], sensitivity=DataSensitivity.SECRET
    )
    assert secret is None or secret.spec.privacy_profile == "local_only"


async def test_an_offline_device_does_not_win_work_that_needs_it(container):
    """The runner-up list is the point: Thursday should say which machine it needs, not
    pick one that is asleep and discover it three steps in."""
    online = container.agents.select(capabilities=["app_control", "os"], device_online=True)
    offline = container.agents.select(capabilities=["app_control", "os"], device_online=False)
    assert online is not None
    assert offline is None or offline.score < online.score


async def test_the_supervisor_escalates_an_unverifiable_result_rather_than_passing_it(
    container, tmp_path
):
    """The supervisor's job is to be unconvinced. An agent that says it worked, with no
    evidence that it did, must not be able to close the loop on its own."""
    session = await connect_failing_node(container, tmp_path)
    response = await container.engine.handle_request(
        UserRequest(
            conversation_id=new_id(), text="Thursday open chrome", device_id=session.device_id
        )
    )
    assert response.verified is False

    task = container.tasks.list()[0]
    assert task.status is TaskState.FAILED
    assert task.verification is not None
    assert task.verification.verdict is AgentVerdict.ESCALATE
    # The report says what was wrong, so the reply has something to be specific about.
    assert task.verification.issues
