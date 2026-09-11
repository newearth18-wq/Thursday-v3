"""First run (EASY INSTALL) — Sprint 64.

The requirement's sharpest sentence about setup:

    "Setup is not considered complete until a real task succeeds."

A wizard that congratulates itself at the end of its own form has told the owner the assistant
works, on no evidence. They close the window believing it and find out at the moment they
first needed it. So COMPLETE is unreachable by answering questions — it is reached by Thursday
actually opening Notepad, verified, and by nothing else.

The second thing these tests hold is the decision count. "ไม่เกินประมาณ 5–7 user decisions" is a
design constraint, not a nicety: every question a normal user cannot answer sends them to a
forum, and anything detectable is detected rather than asked (Sprint 63).
"""

from __future__ import annotations

import pytest
from thursday_core.setup import (
    MAX_DECISIONS,
    SCREENS,
    SetupError,
    SetupStep,
    SetupWizard,
)


class Result:
    """Whatever the test command produced, in the shape `verify` reads."""

    def __init__(self, ok: bool, verified: bool, error: str = "", evidence: dict | None = None):
        self.ok = ok
        self.verified = verified
        self.error = error
        self.evidence = evidence or {}


def answered_through(wizard: SetupWizard, *, upto: SetupStep | None = None) -> SetupWizard:
    """Walk the screens, giving each a plausible answer."""
    values = {
        SetupStep.NAME: "Thursday",
        SetupStep.LANGUAGE: "th",
        SetupStep.VOICE: "thursday-neutral",
        SetupStep.PERMISSIONS: {"apps": True, "files": True, "camera": False},
        SetupStep.AI: "BALANCED",
        SetupStep.TEST_COMMAND: "Thursday เปิด Notepad",
    }
    for screen in SCREENS:
        if upto is not None and screen is upto:
            break
        wizard.answer(screen, values[screen])
    return wizard


# --------------------------------------------------------------------------- the rule


def test_answering_every_question_does_not_finish_setup():
    """The rule this module exists for. Six screens answered, nothing proven."""
    wizard = answered_through(SetupWizard())

    assert wizard.state.step is SetupStep.VERIFYING
    assert wizard.state.complete is False


def test_setup_finishes_when_a_real_command_actually_worked():
    wizard = answered_through(SetupWizard())

    wizard.verify(Result(ok=True, verified=True, evidence={"pid": 4242}))

    assert wizard.state.complete is True
    assert wizard.state.proof["evidence"] == {"pid": 4242}
    assert wizard.state.completed_at is not None


def test_a_command_that_ran_but_was_not_verified_does_not_finish_setup():
    """`ok` says the node did not raise. `verified` says somebody looked and Notepad was
    open. Only the second one is evidence the assistant works (ADR 0012)."""
    wizard = answered_through(SetupWizard())

    wizard.verify(Result(ok=True, verified=False))

    assert wizard.state.complete is False
    assert wizard.state.step is SetupStep.VERIFYING


def test_a_failed_command_leaves_setup_recoverable_rather_than_finished():
    """Staying at VERIFYING is the truth and is retryable — unlike a wizard that closed
    itself and left the owner believing it worked."""
    wizard = answered_through(SetupWizard())

    wizard.verify(Result(ok=False, verified=False, error="Notepad did not open"))
    assert wizard.state.complete is False
    assert "Notepad did not open" in wizard.state.proof["error"]

    wizard.verify(Result(ok=True, verified=True))
    assert wizard.state.complete is True


def test_verifying_before_the_questions_are_answered_is_refused():
    """Otherwise a client could prove a command works and skip the permissions screen —
    landing on a Thursday nobody granted anything to, which the Permission Engine would
    refuse safely and bafflingly."""
    wizard = answered_through(SetupWizard(), upto=SetupStep.PERMISSIONS)

    with pytest.raises(SetupError, match="nothing to verify"):
        wizard.verify(Result(ok=True, verified=True))


# --------------------------------------------------------------------------- the count


def test_the_owner_is_asked_no_more_than_the_requirement_allows():
    """ "ไม่เกินประมาณ 5–7 user decisions". Asserted rather than hoped for: every question
    added later has to displace one, and that friction is the point."""
    wizard = answered_through(SetupWizard())
    assert wizard.state.decisions <= MAX_DECISIONS, wizard.state.answers


def test_nothing_detectable_is_asked():
    """Sprint 63 detects the hardware; the AI screen presents a recommendation rather than a
    question about VRAM. A screen asking something a machine can answer is a screen that
    sends a normal user to a forum."""
    asked = {str(s) for s in SCREENS}
    for detectable in ("GPU", "VRAM", "RAM", "PORT", "DATABASE", "MODEL_NAME", "RUNTIME"):
        assert detectable not in asked


def test_progress_counts_down_in_screens_not_in_percentages():
    wizard = SetupWizard()
    assert wizard.progress()["remaining"] == len(SCREENS)

    wizard.answer(SetupStep.NAME, "Thursday")
    assert wizard.progress()["remaining"] == len(SCREENS) - 1


# --------------------------------------------------------------------------- order


def test_screens_are_answered_in_order():
    wizard = SetupWizard()
    with pytest.raises(SetupError, match="expected"):
        wizard.answer(SetupStep.AI, "BALANCED")


def test_an_empty_answer_is_not_an_answer():
    wizard = SetupWizard()
    for empty in ("", "   ", None):
        with pytest.raises(SetupError, match="needs an answer"):
            wizard.answer(SetupStep.NAME, empty)


def test_a_finished_setup_does_not_accept_more_answers():
    wizard = answered_through(SetupWizard())
    wizard.verify(Result(ok=True, verified=True))

    with pytest.raises(SetupError, match="already finished"):
        wizard.answer(SetupStep.NAME, "Something Else")


# --------------------------------------------------------------------------- resuming


def test_an_interrupted_first_run_resumes_where_it_stopped():
    """People close windows. Restarting at step one is how a first run becomes a second
    attempt somebody does not make."""
    wizard = answered_through(SetupWizard(), upto=SetupStep.AI)
    saved = wizard.state.row()

    resumed = SetupWizard.restore(saved)

    assert resumed.state.step is SetupStep.AI
    assert resumed.state.answers[str(SetupStep.NAME)] == "Thursday"
    assert resumed.state.decisions == 4


def test_completion_is_never_restored_only_the_answers_are():
    """An install verified on a machine that has since had its node removed is not still
    verified. Coming back as VERIFYING costs one command and is true on every restart."""
    wizard = answered_through(SetupWizard())
    wizard.verify(Result(ok=True, verified=True))
    assert wizard.state.complete is True

    resumed = SetupWizard.restore(wizard.state.row())

    assert resumed.state.complete is False
    assert resumed.state.step is SetupStep.VERIFYING
    assert resumed.state.decisions == len(SCREENS), "the answers should have survived"


# --------------------------------------------------------------------------- what is shown


def test_every_step_has_a_sentence_in_the_owners_language():
    """No step numbers to count, no jargon. A wizard that says "STEP 4/6: PERMISSIONS" is
    one written for the person who built it."""
    wizard = SetupWizard()
    seen = set()
    for screen in SCREENS:
        message = wizard.progress()["message"]
        assert message and message not in seen, f"{screen} has no message of its own"
        seen.add(message)
        wizard.answer(screen, "x" if screen is not SetupStep.PERMISSIONS else {"apps": True})

    assert wizard.progress()["message"]


@pytest.mark.parametrize("step", list(SetupStep))
def test_no_message_leaks_a_technical_term(step):
    """The requirement's own list: no Docker, no PostgreSQL, no ports, no model names."""
    from thursday_core.setup import _MESSAGES

    lowered = _MESSAGES[step].lower()
    for jargon in ("docker", "postgres", "redis", "python", "ollama", "port", "api", "token"):
        assert jargon not in lowered, f"{step} says {jargon}"


def test_setup_grants_no_permission_by_itself():
    """§95 — the Permission Engine is the only thing that authorises. A setup answer is an
    input to policy, never a substitute for it, and this asserts the wizard has no way to
    reach the engine at all."""
    import inspect

    from thursday_core import setup as module

    source = inspect.getsource(module)
    for reachable in ("PolicyTable", "PermissionEngine", "decide(", "grant("):
        assert reachable not in source, f"the wizard can reach {reachable}"


# --------------------------------------------------------------------------- through the app


@pytest.fixture
def client(settings, container):
    from fastapi.testclient import TestClient
    from thursday_api.app import create_app

    app = create_app(settings, container=container)
    app.state.container = container
    with TestClient(app) as http:
        yield http


def walk(client) -> None:
    for step, value in [
        ("NAME", "Thursday"),
        ("LANGUAGE", "th"),
        ("VOICE", "thursday-neutral"),
        ("PERMISSIONS", {"apps": True}),
        ("AI", "BALANCED"),
        ("TEST_COMMAND", "Thursday เปิด Notepad"),
    ]:
        response = client.post("/api/v1/setup/answer", params={"step": step}, json=value)
        assert response.status_code == 200, response.text


def test_the_wizard_walks_and_stops_short_of_finished(client):
    walk(client)
    body = client.get("/api/v1/setup").json()

    assert body["step"] == "VERIFYING"
    assert body["complete"] is False


def test_setup_cannot_be_told_that_it_succeeded(client):
    """There is no parameter for it — the same shape as the updater having no parameter for
    a URL (ADR 0033). A completion flag a client can post is one a client will post."""
    import inspect

    from thursday_api.routers import system

    source = inspect.getsource(system.setup_verify)
    for smell in ("ok:", "verified:", "complete=", "success="):
        assert smell not in source, f"verify accepts {smell}"


def test_verifying_with_no_device_says_so_rather_than_failing_obscurely(client):
    """§38, at the least forgiving moment: the owner is at the last screen of their first
    run. "No device is connected yet" is actionable; a stack trace is not."""
    walk(client)
    refused = client.post("/api/v1/setup/verify")

    assert refused.status_code == 409
    assert "no device is connected" in refused.json()["detail"]
    assert client.get("/api/v1/setup").json()["complete"] is False


def test_the_last_screen_is_where_a_real_command_is_run(client, container):
    """The acceptance flow from the requirement, end to end: answer the screens, Thursday
    opens Notepad on a real device session, and only then is setup finished."""
    from thursday_shared.ids import new_id

    opened: list[str] = []

    class Node:
        device_id = new_id()
        name = "Test-PC"
        kind = "desktop"
        os = "Windows"
        transport = "loopback"
        encrypted = True
        capabilities = __import__(
            "thursday_shared.models", fromlist=["DeviceCapabilities"]
        ).DeviceCapabilities.of("app.open")
        telemetry = __import__(
            "thursday_shared.models", fromlist=["DeviceTelemetry"]
        ).DeviceTelemetry()
        last_seen_at = None
        compute = None
        models: tuple = ()

        async def invoke(self, action):
            from thursday_shared.models import DeviceActionResult

            opened.append(action.args.get("app", ""))
            return DeviceActionResult(
                action_id=action.id, ok=True, verified=True, evidence={"pid": 1234}
            )

        async def ping(self):
            return self.telemetry

        async def close(self):
            return None

    client.portal.call(container.hub.register, Node())
    walk(client)

    body = client.post("/api/v1/setup/verify").json()

    assert opened == ["notepad"], "the test command did not actually run"
    assert body["complete"] is True
    assert body["proof"]["evidence"] == {"pid": 1234}
