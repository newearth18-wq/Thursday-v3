"""The bootstrap security checklist, one test per item.

The brief lists ten things to verify before calling the bootstrap complete. A checklist
that is read and nodded at is a checklist that goes stale on the first refactor, so each
item is asserted here instead. If one of these ever fails, the corresponding line in the
README stops being true — which is the point.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from thursday_devices.fake import FakeDeviceNode
from thursday_security.device_auth import DeviceAuthenticator
from thursday_shared.enums import PolicyDecision, TaskState, VoiceMode
from thursday_shared.errors import PermissionDenied, ThursdayError
from thursday_shared.ids import new_id
from thursday_shared.models import (
    ActionRequest,
    DeviceAction,
    PermissionGrant,
    ToolCall,
    UserRequest,
)

from tests.helpers import connect_failing_node

REPO = Path(__file__).resolve().parents[2]


# 1 ----------------------------------------------------------------- unknown command


async def test_an_unknown_node_command_is_rejected(tmp_path):
    node = FakeDeviceNode(allowed_roots=[tmp_path])
    result = await node.executor.execute(DeviceAction(action="registry.edit", args={}))
    assert not result.ok
    assert "unknown action" in result.error


async def test_the_node_runs_only_what_it_has_a_handler_for(tmp_path):
    """The allowlist is the dispatch table itself, so there is no second list to drift."""
    node = FakeDeviceNode(allowed_roots=[tmp_path])
    supported = set(node.executor.supported_actions())
    assert "shell.run" in supported  # exists, and is ASK_ALWAYS by policy
    assert "registry.edit" not in supported
    assert "eval" not in supported


# 2 ----------------------------------------------------------------- invalid token


def test_an_invalid_device_token_is_rejected():
    from thursday_security.device_auth import sign, signing_payload
    from thursday_shared.models import utcnow

    fields = {
        "device_id": str(new_id()),
        "name": "Office-PC",
        "os": "Windows",
        "nonce": "n1",
        "issued_at": utcnow(),
    }
    auth = DeviceAuthenticator("the-real-token")
    assert auth.verify(**fields, signature=sign("the-real-token", signing_payload(**fields)))
    assert not auth.verify(**fields, signature=sign("a-guess", signing_payload(**fields)))


# 3 ----------------------------------------------------------------- offline device


async def test_an_offline_device_is_handled_rather_than_hung(container, tmp_path, session_id):
    node = FakeDeviceNode(name="Sleepy-PC", allowed_roots=[tmp_path], offline_after=0)
    session = node.session()
    await container.hub.register(session)
    container.world.update(active_device_id=session.device_id, active_device_name="Sleepy-PC")

    response = await container.engine.handle_request(
        UserRequest(
            conversation_id=session_id, text="Thursday open chrome", device_id=session.device_id
        )
    )
    assert response.voice_mode is not VoiceMode.SUCCESS
    assert container.tasks.list()[0].status in {TaskState.FAILED, TaskState.BLOCKED}


# 4 ----------------------------------------------------------------- path escape


@pytest.mark.parametrize(
    "path",
    ["/etc/passwd", "../../../../etc/shadow", "~/../../root/.ssh/id_rsa", "/proc/self/environ"],
)
async def test_an_arbitrary_path_is_rejected(tmp_path, path):
    node = FakeDeviceNode(allowed_roots=[tmp_path / "sandbox"])
    (tmp_path / "sandbox").mkdir()
    result = await node.executor.execute(DeviceAction(action="file.read", args={"path": path}))
    assert not result.ok
    assert "allowed roots" in result.error


async def test_an_application_name_cannot_be_an_arbitrary_executable(tmp_path):
    """`app.open` takes a name the adapter resolves, not a path the caller supplies."""
    node = FakeDeviceNode(allowed_roots=[tmp_path])
    result = await node.executor.execute(
        DeviceAction(action="app.open", args={"app": "/tmp/evil.sh; rm -rf ~"})
    )
    # The fake adapter resolves any name, so the assertion is about what was *asked for*:
    # the argument is a name for the adapter's allowlist, never a command line.
    assert result.ok is True
    assert node.adapter.shell_commands == []


# 5 ----------------------------------------------------------------- task transitions


async def test_an_invalid_task_transition_raises(container):
    task = await container.tasks.create(title="t", objective="t")
    with pytest.raises(ThursdayError):
        # NEW cannot jump straight to COMPLETED; VERIFYING is not optional.
        await container.tasks.transition(task.id, TaskState.COMPLETED)


# 6 ----------------------------------------------------------------- BLOCK respected


@pytest.mark.parametrize(
    "action", ["security.disable", "audit.modify", "credential.export", "permission.self_grant"]
)
def test_a_blocked_action_stays_blocked(container, action):
    from thursday_shared.enums import AutonomyLevel

    container.permissions.set_autonomy(AutonomyLevel.HIGH)
    assert container.permissions.decide(ActionRequest(action=action)).decision is (
        PolicyDecision.BLOCK
    )
    # And there is no route around it: not by config, not by grant, not at any autonomy.
    with pytest.raises(PermissionError):
        container.permissions.policy.override(action, PolicyDecision.AUTO)
    with pytest.raises(PermissionError):
        container.permissions.add_grant(PermissionGrant(action=action))


async def test_a_blocked_tool_call_does_not_execute(container, office_pc):
    with pytest.raises((PermissionDenied, ThursdayError)):
        await container.executor.execute(
            ToolCall(tool="security.disable", args={}, device_id=office_pc.device_id),
            agent="computer",
        )


# 7 ------------------------------------------------------- verification gates completion


async def test_a_verification_failure_cannot_become_completed(container, tmp_path, session_id):
    session = await connect_failing_node(container, tmp_path)
    response = await container.engine.handle_request(
        UserRequest(
            conversation_id=session_id, text="Thursday open chrome", device_id=session.device_id
        )
    )
    task = container.tasks.list()[0]
    assert task.status is TaskState.FAILED
    assert response.verified is False
    # And the reply does not claim it worked.
    assert "เรียบร้อย" not in response.text
    assert "Verified" not in response.text


async def test_completion_is_refused_at_the_task_manager_too(container):
    """Belt and braces: even a caller that skipped the orchestrator cannot complete a task
    on a failing verification."""
    from thursday_shared.enums import AgentVerdict
    from thursday_shared.models import VerificationReport

    task = await container.tasks.create(title="t", objective="t")
    await container.tasks.transition(task.id, TaskState.PLANNING)
    await container.tasks.transition(task.id, TaskState.READY)
    await container.tasks.transition(task.id, TaskState.RUNNING)
    await container.tasks.transition(task.id, TaskState.VERIFYING)

    with pytest.raises(ThursdayError):
        await container.tasks.complete(
            task.id,
            result={},
            verification=VerificationReport(verdict=AgentVerdict.ESCALATE, reason="no evidence"),
        )


# 8 ----------------------------------------------------------------- token not logged


def test_the_device_token_never_reaches_a_log_line(capsys):
    from thursday_core.logging import get_logger

    token = "super-secret-enrolment-token"
    auth = DeviceAuthenticator(token)
    outcome = auth.verify(
        device_id=str(new_id()),
        name="X",
        os="Windows",
        nonce="n",
        issued_at=__import__("thursday_shared.models", fromlist=["utcnow"]).utcnow(),
        signature="wrong",
    )
    get_logger("test").warning("device_hello_rejected", reason=outcome.reason)
    captured = capsys.readouterr()
    assert token not in captured.out + captured.err


def test_a_database_password_is_redacted_out_of_the_settings_dump(tmp_path):
    """`settings.redacted()` is what health endpoints and crash reports print."""
    from thursday_core.config import Settings

    settings = Settings(
        data_dir=tmp_path,
        db_driver="postgresql+asyncpg",
        db_host="db.internal",
        db_name="thursday",
        db_user="thursday",
        db_password="hunter2-the-real-one",
    )
    assert "hunter2" in settings.resolved_database_url

    dumped = str(settings.redacted())
    assert "hunter2" not in dumped
    assert "db.internal" in dumped  # the useful part survives


# 9, 10 ------------------------------------------------------- repository hygiene


def test_no_secret_is_committed():
    """The same scan CI runs, called directly so a failure names this checklist item."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "check_no_secrets.py")],
        capture_output=True,
        cwd=REPO,
        check=False,
    )
    assert result.returncode == 0, result.stdout.decode() + result.stderr.decode()


def test_env_files_are_ignored_and_the_example_is_not():
    ignore = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in ignore
    assert "!.env.example" in ignore
    assert (REPO / ".env.example").exists()


def test_no_env_file_is_tracked():
    import subprocess

    tracked = subprocess.run(
        ["git", "ls-files"], capture_output=True, cwd=REPO, check=True
    ).stdout.decode()
    offenders = [
        line
        for line in tracked.splitlines()
        if Path(line).name.startswith(".env") and Path(line).name != ".env.example"
    ]
    assert offenders == []


def test_the_database_url_default_carries_no_password():
    """settings.yaml ships a driver, host and name — never a connection string with
    credentials in it, which is why the URL is composed from parts."""
    shipped = (REPO / "settings.yaml").read_text(encoding="utf-8")
    assert "postgresql://" not in shipped
    assert "password" not in shipped.lower() or "THURSDAY_DB_PASSWORD" in shipped


async def test_a_slow_device_action_times_out(tmp_path):
    node = FakeDeviceNode(allowed_roots=[tmp_path], slow_by=0.5)
    result = await node.executor.execute(
        DeviceAction(action="app.open", args={"app": "slow"}, timeout_s=0.05)
    )
    assert not result.ok
    assert "timed out" in result.error
