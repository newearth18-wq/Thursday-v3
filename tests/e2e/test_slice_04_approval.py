"""Slice 4 — the conversation stops and asks.

The mechanics of the permission engine are covered a layer down. What this slice holds to
is the shape the owner actually experiences: they ask for something consequential, Thursday
stops and explains, they answer, and the work resumes or doesn't. No part of that requires
them to know the word "policy".

Two properties matter more than the happy path. Nothing runs while the question is open —
an approval dialog over an action already taken is theatre. And approving once means once
(ADR 0008): the dangerous set never converts an answer into a standing grant.
"""

from __future__ import annotations

import asyncio

import pytest
from thursday_shared.enums import ApprovalScope, PolicyDecision
from thursday_shared.errors import PermissionDenied
from thursday_shared.models import ToolCall


async def test_the_owner_is_asked_before_the_file_is_gone_not_after(container, office_pc, tmp_path):
    target = tmp_path / "thesis.docx"
    target.write_text("four years of work", encoding="utf-8")

    running = asyncio.create_task(
        container.executor.execute(
            ToolCall(tool="file.delete", args={"path": str(target)}, device_id=office_pc.device_id),
            agent="computer",
        )
    )
    await asyncio.sleep(0.05)

    pending = container.approvals.pending()
    assert len(pending) == 1
    # The file is still there while the question is open. This is the whole point.
    assert target.exists()

    request = pending[0]
    # The dialog has what a person needs to answer: what, where, and what happens if not.
    assert request.expected_outcome
    assert request.consequence_of_refusal
    assert str(target) in request.resource

    await container.approvals.decide(request.id, approve=True, scope=ApprovalScope.ONCE)
    assert (await running).ok
    assert not target.exists()


async def test_saying_no_means_the_action_never_happened(container, office_pc, tmp_path):
    target = tmp_path / "keep.txt"
    target.write_text("keep", encoding="utf-8")

    running = asyncio.create_task(
        container.executor.execute(
            ToolCall(tool="file.delete", args={"path": str(target)}, device_id=office_pc.device_id),
            agent="computer",
        )
    )
    await asyncio.sleep(0.05)
    await container.approvals.decide(container.approvals.pending()[0].id, approve=False)

    with pytest.raises(PermissionDenied):
        await running
    assert target.read_text(encoding="utf-8") == "keep"
    # A refusal is not a lesson: it does not become "don't ask me about this again" either.
    assert container.permissions.list_grants() == []


async def test_yes_to_this_one_is_not_yes_to_all_of_them(container, office_pc, tmp_path):
    """ADR 0008 — the dangerous set offers ONCE and nothing else, so the second file is
    a second question."""
    first, second = tmp_path / "a.txt", tmp_path / "b.txt"
    first.write_text("a", encoding="utf-8")
    second.write_text("b", encoding="utf-8")

    for path in (first, second):
        running = asyncio.create_task(
            container.executor.execute(
                ToolCall(
                    tool="file.delete", args={"path": str(path)}, device_id=office_pc.device_id
                ),
                agent="computer",
            )
        )
        await asyncio.sleep(0.05)
        pending = container.approvals.pending()
        assert len(pending) == 1, f"{path.name} did not raise its own question"
        assert pending[0].scopes_offered == [ApprovalScope.ONCE]
        await container.approvals.decide(pending[0].id, approve=True, scope=ApprovalScope.ALWAYS)
        assert (await running).ok

    assert container.permissions.list_grants() == []


async def test_raising_autonomy_to_the_top_still_does_not_buy_a_deletion(container):
    """PART 97 — the most permissive setting is still not admin."""
    from thursday_shared.enums import AutonomyLevel
    from thursday_shared.models import ActionRequest

    container.permissions.set_autonomy(AutonomyLevel.HIGH)
    assert (
        container.permissions.decide(ActionRequest(action="file.delete")).decision
        is PolicyDecision.ASK_ALWAYS
    )
    assert (
        container.permissions.decide(ActionRequest(action="security.disable")).decision
        is PolicyDecision.BLOCK
    )


async def test_a_broad_instruction_is_not_narrowed_into_a_destructive_one(container, session_id):
    """§96. "Run a shell command" must not become "launch an app called shell command".

    An instruction the rules cannot place goes to the model, and if that cannot place it
    either, it becomes a question — never a guess at something irreversible.
    """
    from thursday_core import intent_rules

    assert intent_rules.parse("Thursday run shell command whoami") is None
    assert intent_rules.parse("Thursday run cmd /c del *.docx") is None

    from thursday_shared.models import UserRequest

    response = await container.engine.handle_request(
        UserRequest(conversation_id=session_id, text="Thursday run cmd /c del *.docx")
    )
    assert response.text
    executed = [
        entry
        for entry in container.audit.entries()
        if entry.result == "ok" and entry.tool in {"shell.run", "file.delete", "app.open"}
    ]
    assert executed == [], f"something ran anyway: {[e.tool for e in executed]}"
