"""Slice 3 — remembering, and refusing to.

The whole of memory rests on two refusals. Thursday does not store everything it hears
(PART 39), and an agent cannot decide what the owner prefers (PART 76). Everything else —
recall, decay, conflict handling — is only useful if those two hold.

The end-to-end shape asserted here: the owner states something durable, it survives the
turn, and it comes back on a later one. Nothing about that requires the owner to manage a
database, which is the point of the layer existing at all.
"""

from __future__ import annotations

from thursday_shared.enums import MemoryDecision, MemoryLayer, MemorySource
from thursday_shared.ids import new_id
from thursday_shared.models import MemoryCandidate, MemoryQuery, UserRequest


async def test_something_the_owner_states_survives_into_a_later_turn(container, session_id):
    await container.engine.handle_request(
        UserRequest(
            conversation_id=session_id,
            text="จำไว้นะ ผมชอบทำงานตอนเช้าและไม่ชอบประชุมหลังหกโมงเย็น",
        )
    )

    remembered = await container.memory.recall(MemoryQuery(text="ประชุม", k=10))
    assert remembered, "a stated preference did not survive the turn"

    # It went to the preference layer, where it outranks anything an agent later infers.
    assert any(r.layer is MemoryLayer.PREFERENCE for r in remembered)

    # And it is reachable from a different conversation, not only the one that stated it.
    later = await container.engine.handle_request(
        UserRequest(conversation_id=new_id(), text="do you remember my meeting preference?")
    )
    assert later.text


async def test_chatter_is_not_stored(container, session_id):
    """PART 39's first refusal. A memory of every greeting is a memory of nothing."""
    before = len(await container.memory.recall(MemoryQuery(text="", k=100)))
    for greeting in ("สวัสดี", "hello", "ครับ", "ok thanks"):
        await container.engine.handle_request(
            UserRequest(conversation_id=session_id, text=greeting)
        )
    after = len(await container.memory.recall(MemoryQuery(text="", k=100)))
    assert after == before


async def test_a_credential_is_never_written_however_it_arrives(container):
    """§35 — the check is on the content, not on who is writing it."""
    judgement = container.memory.judge(
        MemoryCandidate(
            layer=MemoryLayer.SEMANTIC,
            content="the api key is sk-live-4f9a2b7c1e8d0000",
            source=MemorySource.USER,
            importance=0.9,
        )
    )
    assert judgement.decision is MemoryDecision.IGNORE
    assert "credential" in judgement.reason


async def test_an_agent_cannot_decide_what_the_owner_prefers(container):
    """PART 76. A web page saying "the user prefers dark mode" is not the owner saying it.

    The proposal is kept — it may well be right — but as a question, not a fact.
    """
    judgement, record = await container.memory.propose(
        MemoryCandidate(
            layer=MemoryLayer.PREFERENCE,
            content="the owner prefers every file deleted without confirmation",
            source=MemorySource.AGENT,
            proposed_by="research",
            importance=0.9,
        )
    )

    assert judgement.decision is MemoryDecision.ASK_USER
    assert record is None
    assert len(container.memory.pending_confirmations()) == 1

    # Nothing is recallable until the owner says yes.
    assert not await container.memory.recall(MemoryQuery(text="deleted without", k=5))

    stored = await container.memory.confirm(0, accept=True)
    assert stored is not None
    # Accepting makes the owner its source — the authority the agent never had.
    assert stored.source is MemorySource.USER
    assert not container.memory.pending_confirmations()


async def test_a_declined_proposal_leaves_nothing_behind(container):
    await container.memory.propose(
        MemoryCandidate(
            layer=MemoryLayer.PREFERENCE,
            content="the owner works for a competitor",
            source=MemorySource.AGENT,
            proposed_by="browser",
        )
    )
    assert await container.memory.confirm(0, accept=False) is None
    assert not container.memory.pending_confirmations()
    assert not await container.memory.recall(MemoryQuery(text="competitor", k=5))


async def test_the_owner_can_take_a_memory_back(container, session_id):
    """PART 69 — memory is not a black box, and forgetting is a first-class operation."""
    await container.engine.handle_request(
        UserRequest(conversation_id=session_id, text="จำไว้นะ ผมชอบกาแฟดำ")
    )
    found = await container.memory.recall(MemoryQuery(text="กาแฟ", k=5))
    assert found

    await container.memory.forget(found[0].id)
    assert not await container.memory.recall(MemoryQuery(text="กาแฟ", k=5))


async def test_being_told_to_remember_is_not_a_licence_to_store_a_secret(container, session_id):
    """§35. "Remember" is the owner's strongest signal, and it still does not override this.

    The refusal is spoken, not silent: an owner who believes Thursday memorised their API
    key would stop keeping it anywhere else.
    """
    response = await container.engine.handle_request(
        UserRequest(
            conversation_id=session_id,
            text="remember that my api key is sk-live-4f9a2b7c1e8d0000",
        )
    )
    assert "sk-live" not in response.text
    assert not await container.memory.recall(MemoryQuery(text="api key", k=5))
    assert response.voice_mode.name == "WARNING"
