"""The rest of the agent bench, and learning a skill by watching (§15, §51, V9)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from thursday_agents.calendar import CalendarAgent
from thursday_agents.coding import CodingAgent, _extract_patch, looks_like_code
from thursday_agents.communication import CommunicationAgent
from thursday_agents.design import DesignAgent, contrast, luminance
from thursday_agents.files import FileAgent, group_duplicates
from thursday_agents.media import MediaAgent, identify
from thursday_agents.ports import (
    CalendarEvent,
    LocalCalendar,
    LocalOutbox,
    Message,
    parse_recipients,
)
from thursday_automation.skills.learning import SkillObserver
from thursday_shared.enums import PermissionLevel
from thursday_shared.ids import new_id
from thursday_shared.models import Event, JobContract, ToolResult


class StubContext:
    """Enough of an `AgentContext` to exercise an agent's decisions."""

    def __init__(self, *, tool_results: dict | None = None, text: str = "") -> None:
        self._tools = tool_results or {}
        self._text = text
        self.calls: list = []

    async def call_tool(self, call):
        self.calls.append(call)
        result = self._tools.get(call.tool)
        if result is None:
            return ToolResult(call_id=call.id, tool=call.tool, ok=False, error="not stubbed")
        return result

    async def think(self, request):
        class _R:
            text = self._text

        return _R()


def contract(**inputs) -> JobContract:
    return JobContract(
        task_id=new_id(), step_id=new_id(), agent="test", objective="do the thing", inputs=inputs
    )


# ------------------------------------------------------------------ file agent


def test_duplicates_by_hash_are_grouped():
    files = [
        {"path": "/a.txt", "sha256": "aa", "size": 10, "name": "a.txt"},
        {"path": "/b.txt", "sha256": "aa", "size": 10, "name": "b.txt"},
        {"path": "/c.txt", "sha256": "bb", "size": 20, "name": "c.txt"},
    ]
    groups = group_duplicates(files)
    assert len(groups) == 1
    assert {f["path"] for f in groups[0]} == {"/a.txt", "/b.txt"}


def test_files_that_merely_look_alike_are_still_grouped_but_by_a_weaker_key():
    """A hash match means the bytes are identical. A name-and-size match means two files
    look alike, which is a good reason to show someone a pair and a bad reason to call them
    copies — so the agent says which test it used."""
    files = [
        {"path": "/x/report.pdf", "name": "report.pdf", "size": 100},
        {"path": "/y/report.pdf", "name": "report.pdf", "size": 100},
    ]
    assert len(group_duplicates(files)) == 1


async def test_the_file_agent_says_when_it_did_not_see_the_whole_folder():
    """ "No duplicates" over a truncated listing is a different statement from "no
    duplicates"."""
    agent = FileAgent()
    ctx = StubContext(
        tool_results={
            "file.search": ToolResult(
                call_id=uuid4(),
                tool="file.search",
                ok=True,
                data={"files": [{"path": "/a", "name": "a", "size": 1}], "truncated": True},
            )
        }
    )
    result = await agent.execute(contract(root="~", question="what is here"), ctx)
    assert result.ok
    assert result.output["truncated"] is True
    assert "not the whole folder" in result.output["summary"]


def test_the_file_agent_cannot_modify_anything():
    """Finding duplicates and deleting them are different jobs, and one bad grouping away
    from deleting the only copy of something."""
    assert FileAgent.spec.permission_ceiling is PermissionLevel.READ
    assert all(
        not t.startswith(("file.delete", "file.write", "file.move")) for t in FileAgent.spec.tools
    )


# ------------------------------------------------------------------ coding agent


@pytest.mark.parametrize("path", ["a.py", "b.tsx", "c.rs", "d.toml"])
def test_source_files_are_read(path):
    assert looks_like_code(path)


@pytest.mark.parametrize("path", ["id_rsa", "photo.png", "notes"])
def test_other_files_are_not(path):
    assert not looks_like_code(path)


async def test_the_coding_agent_refuses_a_non_source_file():
    agent = CodingAgent()
    result = await agent.execute(contract(path="/home/x/.ssh/id_rsa"), StubContext())
    assert not result.ok
    assert "does not look like source" in result.error


async def test_the_coding_agent_refuses_to_guess_a_file():
    agent = CodingAgent()
    result = await agent.execute(contract(), StubContext())
    assert not result.ok
    assert "will not guess" in result.error


async def test_a_proposed_patch_is_never_marked_applied():
    """A proposal that anything downstream could read as a change is the failure this
    agent's whole shape exists to prevent."""
    agent = CodingAgent()
    ctx = StubContext(
        tool_results={
            "file.read": ToolResult(
                call_id=uuid4(), tool="file.read", ok=True, data={"content": "x = 1\n"}
            )
        },
        text="Change it.\n```python\nx = 2\n```",
    )
    result = await agent.execute(contract(path="a.py", question="make it two"), ctx)
    assert result.ok
    assert result.output["applied"] is False
    assert result.output["patch"] == "x = 2"


def test_a_reply_with_no_code_block_yields_no_patch():
    """A `patch` field holding prose is worse than an empty one: something downstream will
    eventually try to write it to a file."""
    assert _extract_patch("This file looks fine to me.") is None


def test_the_coding_agent_holds_no_shell():
    """An agent that shelled out to "just check the tests pass" would be asking the owner
    to approve arbitrary execution on the strength of a summary they cannot verify."""
    assert not any(t.startswith(("shell", "powershell")) for t in CodingAgent.spec.tools)
    assert CodingAgent.spec.permission_ceiling is PermissionLevel.READ


# ------------------------------------------------------------------ media agent


def test_a_png_is_identified_from_its_header():
    header = (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + (800).to_bytes(4, "big")
        + (600).to_bytes(4, "big")
    )
    info = identify(header)
    assert info.format == "PNG"
    assert (info.width, info.height) == (800, 600)


def test_a_jpeg_renamed_as_a_png_is_still_a_jpeg():
    """An extension is a claim by whoever named the file; a header is evidence."""
    jpeg = b"\xff\xd8\xff\xe0" + b"\x00\x10JFIF\x00" + b"\x00" * 8
    assert identify(jpeg).format == "JPEG"


def test_an_unrecognised_file_says_so_rather_than_guessing():
    info = identify(b"just some text in a file, honestly")
    assert info.format == ""
    assert info.describe() == "unrecognised format"


def test_a_truncated_file_is_not_identified():
    assert identify(b"\x89PNG").format == ""


async def test_the_media_agent_identifies_a_file_and_reports_no_modification():
    """Through the agent, not just the parser: a 30x20 GIF read as latin-1 bytes must come
    back identified. The header has to survive the node's encoding to be worth anything."""
    agent = MediaAgent()
    gif = b"GIF89a" + (30).to_bytes(2, "little") + (20).to_bytes(2, "little") + b"\x00" * 8
    ctx = StubContext(
        tool_results={
            "file.read": ToolResult(
                call_id=uuid4(),
                tool="file.read",
                ok=True,
                data={"content": gif.decode("latin-1")},
            )
        }
    )
    result = await agent.execute(contract(path="/x.gif"), ctx)
    assert result.ok
    assert result.output["info"]["format"] == "GIF"
    assert (result.output["info"]["width"], result.output["info"]["height"]) == (30, 20)
    assert result.output["modified"] is False


async def test_the_media_agent_reads_only_a_header():
    """Reading a two-gigabyte video to answer "what is this" would be absurd."""
    agent = MediaAgent()
    ctx = StubContext(
        tool_results={
            "file.read": ToolResult(
                call_id=uuid4(), tool="file.read", ok=True, data={"content": ""}
            )
        }
    )
    await agent.execute(contract(path="/x.mp4"), ctx)
    assert ctx.calls[0].args["bytes"] == 512


# ------------------------------------------------------------------ design agent


def test_contrast_is_computed_not_asserted():
    """Black on white is the textbook maximum; the sRGB curve has to be applied for it to
    come out right."""
    assert contrast("#000000", "#ffffff") == 21.0
    assert luminance("#ffffff") == pytest.approx(1.0)
    assert luminance("#000000") == pytest.approx(0.0)


def test_shorthand_hex_is_understood():
    assert contrast("#000", "#fff") == 21.0


async def test_a_failing_palette_is_reported_as_failing():
    """A palette that fails legibility looks fine in the spec and is unreadable on the
    screen. The arithmetic is cheap enough that guessing has no excuse."""
    agent = DesignAgent()
    result = await agent.execute(
        contract(intent="a dashboard", tokens={"text": "#cccccc", "surface": "#ffffff"}),
        StubContext(text="- header\n- chart\n"),
    )
    assert result.ok
    failing = [row for row in result.output["contrast"] if not row["passes"]]
    assert failing, "grey on white must not pass AA"
    assert "fail AA contrast" in result.output["summary"]


async def test_the_design_agent_does_not_claim_an_image():
    agent = DesignAgent()
    result = await agent.execute(contract(intent="a landing page"), StubContext(text="- hero\n"))
    assert result.output["rendered"] is False


# ------------------------------------------------------------------ calendar agent


@pytest.fixture
def calendar() -> LocalCalendar:
    return LocalCalendar()


async def test_events_are_returned_in_time_order(calendar):
    base = datetime(2026, 3, 9, 9, 0, tzinfo=UTC)
    await calendar.create(CalendarEvent(title="second", start=base + timedelta(hours=2)))
    await calendar.create(CalendarEvent(title="first", start=base))

    events = await calendar.events(start=base - timedelta(days=1), end=base + timedelta(days=1))
    assert [e.title for e in events] == ["first", "second"]


async def test_a_clash_is_reported_and_the_entry_is_not_added(calendar):
    """An assistant that schedules over an existing commitment has not helped, and the
    failure is quiet: the new entry looks perfectly fine on its own."""
    base = datetime(2026, 3, 9, 9, 0, tzinfo=UTC)
    await calendar.create(CalendarEvent(title="Standup", start=base, end=base + timedelta(hours=1)))

    agent = CalendarAgent(calendar)
    result = await agent.execute(
        contract(start=base.isoformat(), title="Review", at=base.isoformat(), minutes=30),
        StubContext(),
    )
    assert result.output["conflicts"]
    assert result.output["created"] is False
    assert "clashes with Standup" in result.output["summary"]


async def test_a_free_slot_is_offered_not_taken(calendar):
    base = datetime(2026, 3, 9, 9, 0, tzinfo=UTC)
    agent = CalendarAgent(calendar)
    result = await agent.execute(
        contract(start=base.isoformat(), title="Review", at=base.isoformat()), StubContext()
    )
    assert not result.output["conflicts"]
    assert result.output["created"] is False
    assert await calendar.events(start=base, end=base + timedelta(days=1)) == []


def test_creating_an_entry_is_above_this_agents_ceiling():
    """A calendar entry is a promise to other people. Making one notifies them."""
    assert CalendarAgent.spec.permission_ceiling < PermissionLevel.EXTERNAL


# ------------------------------------------------------------------ communication agent


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("a@x.com, b@x.com", ("a@x.com", "b@x.com")),
        ("a@x.com; b@x.com", ("a@x.com", "b@x.com")),
        (["a@x.com"], ("a@x.com",)),
        (None, ()),
        ("", ()),
    ],
)
def test_recipients_are_normalised(raw, expected):
    assert parse_recipients(raw) == expected


async def test_a_message_with_no_recipient_is_refused():
    """There is no sensible default recipient, and the failure mode of guessing one is a
    message in a stranger's inbox."""
    agent = CommunicationAgent(LocalOutbox())
    result = await agent.execute(contract(intent="say hello"), StubContext())
    assert not result.ok
    assert "will not guess" in result.error


async def test_the_communication_agent_only_ever_drafts():
    """The one action in this system with neither an undo nor a verification."""
    outbox = LocalOutbox()
    agent = CommunicationAgent(outbox)
    result = await agent.execute(
        contract(to="a@x.com", intent="ask about Thursday"), StubContext(text="Hello.")
    )

    assert result.ok
    assert result.output["sent"] is False
    stored = await outbox.outbox()
    assert len(stored) == 1
    assert stored[0].is_draft


async def test_sending_is_stamped_by_the_provider_not_the_caller():
    """A `sent_at` the caller could pass in is one that gets passed in by accident."""
    outbox = LocalOutbox()
    draft = await outbox.draft(Message(to=("a@x.com",), body="hi"))
    assert draft.is_draft

    sent = await outbox.send(draft.id)
    assert not sent.is_draft
    assert sent.sent_at is not None


# ------------------------------------------------------------------ skill learning


def step_event(task_id, *, agent: str, action: str | None = None, args: dict | None = None):
    return Event(
        kind="task.step.completed",
        task_id=task_id,
        payload={
            "step": agent,
            "agent": agent,
            "action": action,
            "args": args or {},
            "verified": True,
        },
    )


def done(task_id):
    return Event(kind="task.completed", task_id=task_id, payload={})


async def run_workflow(observer: SkillObserver, *, path: str) -> None:
    task = new_id()
    await observer.on_step(
        step_event(task, agent="computer", action="file.read", args={"path": path})
    )
    await observer.on_step(step_event(task, agent="data", args={"pass_mark": 50}))
    await observer.on_task_finished(done(task))


async def test_a_workflow_done_twice_is_proposed():
    observer = SkillObserver()
    await run_workflow(observer, path="/a.csv")
    assert observer.proposals() == []  # once is an event

    await run_workflow(observer, path="/b.csv")
    proposals = observer.proposals()
    assert len(proposals) == 1
    assert proposals[0].signature == ("file.read", "data")
    assert proposals[0].runs == 2


async def test_arguments_that_varied_become_the_workflows_inputs():
    """The information that turns a recording into something reusable, and it falls out of
    comparing the runs for free."""
    observer = SkillObserver()
    await run_workflow(observer, path="/a.csv")
    await run_workflow(observer, path="/b.csv")

    proposal = observer.proposals()[0]
    assert proposal.parameters == ("path",)
    # The path varied, so it is not baked into the step; the pass mark did not, so it is.
    assert proposal.steps[0].args == {}
    assert proposal.steps[1].args == {"pass_mark": 50}


async def test_order_is_what_makes_two_runs_the_same_workflow():
    """ "Read then analyse" and "analyse then read" contain identical steps and only one of
    them is a thing anybody does."""
    observer = SkillObserver()
    forward, backward = new_id(), new_id()
    await observer.on_step(step_event(forward, agent="computer", action="file.read"))
    await observer.on_step(step_event(forward, agent="data"))
    await observer.on_task_finished(done(forward))

    await observer.on_step(step_event(backward, agent="data"))
    await observer.on_step(step_event(backward, agent="computer", action="file.read"))
    await observer.on_task_finished(done(backward))

    assert observer.proposals() == []


async def test_a_run_that_did_not_verify_is_not_learned():
    """Learning it would mean offering the owner a workflow whose first outing repeats
    their bad afternoon."""
    observer = SkillObserver()
    task = new_id()
    await observer.on_step(step_event(task, agent="computer", action="file.read"))
    bad = step_event(task, agent="data")
    bad.payload["verified"] = False
    await observer.on_step(bad)
    await observer.on_task_finished(done(task))

    assert len(observer) == 0


async def test_a_single_step_run_is_not_a_workflow():
    observer = SkillObserver()
    for _ in range(3):
        task = new_id()
        await observer.on_step(step_event(task, agent="computer", action="app.open"))
        await observer.on_task_finished(done(task))
    assert observer.proposals() == []


async def test_a_cancelled_run_does_not_leak():
    observer = SkillObserver()
    task = new_id()
    await observer.on_step(step_event(task, agent="computer", action="file.read"))
    await observer.on_task_finished(Event(kind="task.cancelled", task_id=task, payload={}))
    assert observer._open == {}


async def test_the_owner_is_asked_once():
    """An assistant that keeps asking is one people stop reading."""
    observer = SkillObserver()
    await run_workflow(observer, path="/a.csv")
    await run_workflow(observer, path="/b.csv")

    proposal = observer.unproposed()[0]
    observer.mark_proposed(proposal)
    assert observer.unproposed() == []


async def test_an_adopted_proposal_is_a_draft(container):
    """It was watched, not reviewed. The sandbox and approval exist for exactly this."""
    observer = SkillObserver()
    await run_workflow(observer, path="/a.csv")
    await run_workflow(observer, path="/b.csv")

    skill = container.skills.adopt(observer.proposals()[0], name="Grade Report")
    assert skill.status.value == "draft"
    assert "learned" in skill.tags
    assert skill.latest is not None
    # What the owner has to supply each run is written down rather than discovered when a
    # step fails.
    assert skill.latest.input_schema == {"path": "string"}
