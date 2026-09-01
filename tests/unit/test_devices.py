"""Node execution, verification, path confinement and device routing (§9, §19–22)."""

from __future__ import annotations

from pathlib import Path

import pytest
from thursday_core.device_router import DeviceRouter
from thursday_devices import actions as catalogue
from thursday_devices.hub import DeviceHub, LoopbackDeviceSession
from thursday_devices.node.executor import NodeExecutor
from thursday_shared.enums import ControlTier
from thursday_shared.errors import DeviceActionFailed, DeviceUnavailable
from thursday_shared.ids import new_id
from thursday_shared.models import DeviceAction, WorldStateSnapshot
from thursday_shared.protocol import Hello, dump_frame, parse_frame

from tests.conftest import FakeAdapter


@pytest.fixture
def executor(tmp_path: Path) -> NodeExecutor:
    return NodeExecutor(FakeAdapter(), allowed_roots=[tmp_path])


async def test_a_launch_is_verified_by_observing_the_process(executor):
    result = await executor.execute(DeviceAction(action="open_app", args={"name": "chrome"}))
    assert result.ok and result.verified
    assert result.evidence["process_count"] == 1
    assert result.evidence["active_window"] == "chrome — window"
    assert result.undo is not None and result.undo.operation == "close_app"


async def test_a_launch_that_leaves_no_process_is_reported_unverified(tmp_path):
    executor = NodeExecutor(FakeAdapter(fail_launch=True), allowed_roots=[tmp_path])
    result = await executor.execute(DeviceAction(action="open_app", args={"name": "chrome"}))
    assert result.ok is True  # the command itself did not error
    assert result.verified is False  # ...but nothing confirms it worked
    assert result.succeeded is False  # so it is not a success
    assert result.undo is None


async def test_a_write_is_verified_by_reading_it_back(executor, tmp_path):
    target = tmp_path / "notes" / "a.txt"
    result = await executor.execute(
        DeviceAction(action="write_file", args={"path": str(target), "content": "สวัสดี"})
    )
    assert result.ok and result.verified
    assert target.read_text(encoding="utf-8") == "สวัสดี"
    assert result.undo.previous_state == {"absent": True}


async def test_a_write_over_an_existing_file_keeps_the_old_contents_for_undo(executor, tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("before", encoding="utf-8")
    result = await executor.execute(
        DeviceAction(action="write_file", args={"path": str(target), "content": "after"})
    )
    assert result.undo.previous_state == {"content": "before"}


async def test_delete_quarantines_rather_than_destroying(executor, tmp_path):
    target = tmp_path / "gone.txt"
    target.write_text("x", encoding="utf-8")
    result = await executor.execute(DeviceAction(action="delete", args={"path": str(target)}))
    assert result.verified and not target.exists()
    assert Path(result.data["moved_to"]).exists()
    assert result.undo.operation == "restore_from_trash"


async def test_the_node_refuses_paths_outside_its_allowed_roots(executor):
    result = await executor.execute(
        DeviceAction(action="write_file", args={"path": "/etc/thursday-test", "content": "x"})
    )
    assert not result.ok
    assert "outside this node's allowed roots" in result.error


async def test_traversal_out_of_a_root_is_refused(executor, tmp_path):
    result = await executor.execute(
        DeviceAction(
            action="read_file", args={"path": str(tmp_path / ".." / ".." / "etc" / "passwd")}
        )
    )
    assert not result.ok


async def test_missing_arguments_are_refused_before_anything_runs(executor):
    result = await executor.execute(DeviceAction(action="open_app", args={}))
    assert not result.ok and "missing required args" in result.error


async def test_an_unknown_action_is_refused(executor):
    result = await executor.execute(DeviceAction(action="hack_the_mainframe"))
    assert not result.ok and "unknown action" in result.error


async def test_an_unsupported_capability_is_refused_by_the_node(tmp_path):
    adapter = FakeAdapter()
    adapter.capabilities = lambda: __import__(
        "thursday_shared.models", fromlist=["DeviceCapabilities"]
    ).DeviceCapabilities(open_app=True)
    executor = NodeExecutor(adapter, allowed_roots=[tmp_path])
    result = await executor.execute(DeviceAction(action="run_shell", args={"command": "ls"}))
    assert not result.ok and "does not support" in result.error


async def test_the_hub_refuses_an_unadvertised_action_before_dispatch(tmp_path):
    adapter = FakeAdapter()
    from thursday_shared.models import DeviceCapabilities

    adapter.capabilities = lambda: DeviceCapabilities(open_app=True)
    hub = DeviceHub()
    session = LoopbackDeviceSession(
        device_id=new_id(),
        name="Limited-PC",
        executor=NodeExecutor(adapter, allowed_roots=[tmp_path]),
    )
    await hub.register(session)
    with pytest.raises(DeviceActionFailed, match="does not support"):
        await hub.invoke(session.device_id, DeviceAction(action="screenshot"))


async def test_invoking_a_disconnected_device_says_so(tmp_path):
    hub = DeviceHub()
    session = LoopbackDeviceSession(
        device_id=new_id(),
        name="Gone-PC",
        executor=NodeExecutor(FakeAdapter(), allowed_roots=[tmp_path]),
    )
    await hub.register(session)
    await hub.unregister(session.device_id)
    with pytest.raises(DeviceUnavailable):
        await hub.invoke(session.device_id, DeviceAction(action="system_info"))


def test_gui_control_is_the_last_tier_in_the_catalogue():
    """§19 — nothing in the built-in catalogue reaches for coordinate clicking."""
    assert all(spec.control_tier < ControlTier.GUI for spec in catalogue.CATALOGUE.values())


def test_every_mutating_action_declares_its_reversibility():
    for name in ("delete", "run_shell", "power"):
        assert catalogue.get(name).reversible is False
    for name in ("move", "copy", "create_folder"):
        assert catalogue.get(name).reversible is True


def test_protocol_frames_round_trip():
    from thursday_shared.models import DeviceCapabilities, DeviceTelemetry

    hello = Hello(
        device_id=new_id(),
        name="Office-PC",
        os="Windows",
        capabilities=DeviceCapabilities(open_app=True),
        telemetry=DeviceTelemetry(),
        nonce="abc",
    )
    decoded = parse_frame(dump_frame(hello))
    assert isinstance(decoded, Hello)
    assert decoded.name == "Office-PC"
    assert decoded.capabilities.supports("open_app")
    assert not decoded.capabilities.supports("camera")


def test_an_unknown_or_mismatched_frame_is_rejected():
    with pytest.raises(ValueError, match="unknown frame type"):
        parse_frame('{"type": "NONSENSE", "v": 1}')
    with pytest.raises(ValueError, match="unsupported protocol version"):
        parse_frame('{"type": "HEARTBEAT", "v": 99}')


# ------------------------------------------------------------------ device routing


@pytest.fixture
async def two_devices(tmp_path):
    hub = DeviceHub()
    office, home = new_id(), new_id()
    await hub.register(
        LoopbackDeviceSession(
            device_id=office,
            name="Office-PC",
            executor=NodeExecutor(FakeAdapter(), allowed_roots=[tmp_path]),
        ),
        location_context="office",
    )
    await hub.register(
        LoopbackDeviceSession(
            device_id=home,
            name="Home-Laptop",
            kind="laptop",
            executor=NodeExecutor(FakeAdapter(), allowed_roots=[tmp_path]),
        ),
        location_context="home",
    )
    return hub, office, home


@pytest.mark.parametrize(
    ("hint", "expected"),
    [
        ("this", "Office-PC"),
        ("เครื่องนี้", "Office-PC"),
        ("Home-Laptop", "Home-Laptop"),
        ("home-lap", "Home-Laptop"),
        ("โน้ตบุ๊ก", "Home-Laptop"),
        ("laptop", "Home-Laptop"),
    ],
)
async def test_device_references_resolve(two_devices, hint, expected):
    hub, office, _ = two_devices
    resolution = DeviceRouter(hub).resolve(
        hint, world=WorldStateSnapshot(active_device_id=office), origin_device_id=office
    )
    assert resolution.device is not None and resolution.device.name == expected
    assert not resolution.needs_confirmation


async def test_an_ambiguous_reference_becomes_a_question(two_devices):
    hub, office, _ = two_devices
    resolution = DeviceRouter(hub).resolve(
        "the other one", world=WorldStateSnapshot(active_device_id=office)
    )
    assert resolution.device is None
    assert resolution.needs_confirmation
    assert "Office-PC" in resolution.question() and "Home-Laptop" in resolution.question()


async def test_an_explicit_location_narrows_and_never_widens(two_devices):
    """'the PC at home' must not quietly become the office PC."""
    hub, office, _ = two_devices
    resolution = DeviceRouter(hub).resolve(
        "คอมที่บ้าน", world=WorldStateSnapshot(active_device_id=office), origin_device_id=office
    )
    assert resolution.device is None
    assert "home" in resolution.reason


async def test_a_required_capability_filters_the_candidates(two_devices):
    hub, office, _ = two_devices
    resolution = DeviceRouter(hub).resolve(
        "this",
        world=WorldStateSnapshot(active_device_id=office),
        origin_device_id=office,
        required_capability="camera",
    )
    assert resolution.device is None
