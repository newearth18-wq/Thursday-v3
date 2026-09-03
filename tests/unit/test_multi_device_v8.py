"""Conversational device focus, trust, and the remote-command gate (§9, §22, V8)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from thursday_core.device_router import DeviceRouter
from thursday_core.focus import DeviceFocus
from thursday_core.intent_rules import parse
from thursday_devices.actions import CATALOGUE
from thursday_security.permissions import PermissionEngine
from thursday_security.remote import RemoteCommandGate, needs_confirmation_when_remote
from thursday_shared.enums import (
    DeviceStatus,
    IntentKind,
    PermissionLevel,
    PolicyDecision,
    TrustLevel,
)
from thursday_shared.ids import new_id
from thursday_shared.models import (
    ActionRequest,
    ConversationTurn,
    DeviceCapabilities,
    DeviceSummary,
    Intent,
    PermissionGrant,
    WorldStateSnapshot,
)


def device(
    name: str,
    *,
    kind: str = "desktop",
    location: str | None = None,
    trust: TrustLevel = TrustLevel.LIMITED,
    encrypted: bool = True,
    seen: datetime | None = None,
) -> DeviceSummary:
    return DeviceSummary(
        id=new_id(),
        name=name,
        kind=kind,
        os="FakeOS",
        status=DeviceStatus.ONLINE,
        capabilities=DeviceCapabilities.of("app", "file", "system", "audio"),
        location_context=location,
        trust_level=trust,
        encrypted=encrypted,
        last_seen_at=seen or datetime.now(UTC),
    )


class StubHub:
    def __init__(self, *devices: DeviceSummary) -> None:
        self._devices = list(devices)

    def online(self) -> list[DeviceSummary]:
        return [d for d in self._devices if d.status is DeviceStatus.ONLINE]


# ------------------------------------------------------------------ focus


def test_focus_expires_rather_than_lingering():
    """A device named twenty minutes ago is not what "it" means now."""
    focus = DeviceFocus()
    session = new_id()
    start = datetime(2026, 3, 7, 12, 0, tzinfo=UTC)
    focus.remember(session, device_id=new_id(), device_name="Home-PC", reason="asked", now=start)

    assert focus.current(session, now=start + timedelta(seconds=30)) is not None
    assert focus.current(session, now=start + timedelta(minutes=20)) is None


def test_focus_is_per_conversation():
    """A conversation on the laptop must not steer one on the phone."""
    focus = DeviceFocus()
    laptop_chat, phone_chat = new_id(), new_id()
    focus.remember(laptop_chat, device_id=new_id(), device_name="Home-PC", reason="asked")

    assert focus.current(laptop_chat) is not None
    assert focus.current(phone_chat) is None


def test_an_inherited_focus_is_flagged_for_announcement():
    """The reply has to say where the work went. That is the whole safety story here."""
    focus = DeviceFocus()
    session = new_id()
    set_now = focus.remember(session, device_id=new_id(), device_name="Home-PC", reason="asked")
    assert not set_now.should_announce  # the turn that set it already knows

    inherited = focus.current(session)
    assert inherited is not None and inherited.should_announce


# ------------------------------------------------------------------ routing precedence


@pytest.fixture
def two_devices() -> tuple[DeviceSummary, DeviceSummary, DeviceRouter]:
    home = device("Home-PC", location="home")
    phone = device("Pixel", kind="phone")
    return home, phone, DeviceRouter(StubHub(home, phone))


def test_focus_beats_the_device_the_owner_is_holding(two_devices):
    """The V8 acceptance case, at the router level."""
    home, phone, router = two_devices
    focus = DeviceFocus()
    session = new_id()
    focus.remember(session, device_id=home.id, device_name=home.name, reason="you asked about it")

    resolution = router.resolve(
        None,
        world=WorldStateSnapshot(active_device_id=phone.id),
        origin_device_id=phone.id,
        focus=focus.current(session),
    )
    assert resolution.device is home
    assert resolution.announce  # and the caller must say so


def test_saying_this_machine_overrides_the_focus(two_devices):
    """ "เครื่องนี้" moves the subject back, and saying so must be enough."""
    home, phone, router = two_devices
    focus = DeviceFocus()
    session = new_id()
    focus.remember(session, device_id=home.id, device_name=home.name, reason="you asked about it")

    resolution = router.resolve(
        "เครื่องนี้",
        world=WorldStateSnapshot(active_device_id=phone.id),
        origin_device_id=phone.id,
        focus=focus.current(session),
    )
    assert resolution.device is phone
    assert not resolution.announce


def test_naming_a_device_outright_still_wins(two_devices):
    home, phone, router = two_devices
    focus = DeviceFocus()
    session = new_id()
    focus.remember(session, device_id=home.id, device_name=home.name, reason="you asked about it")

    resolution = router.resolve(
        "Pixel",
        world=WorldStateSnapshot(active_device_id=home.id),
        origin_device_id=home.id,
        focus=focus.current(session),
    )
    assert resolution.device is phone


def test_a_focus_on_a_device_that_went_offline_falls_through(two_devices):
    """Not an error and not a wrong machine: the next-best anchor, as if no focus existed."""
    home, phone, _ = two_devices
    home.status = DeviceStatus.OFFLINE
    router = DeviceRouter(StubHub(home, phone))
    focus = DeviceFocus()
    session = new_id()
    focus.remember(session, device_id=home.id, device_name=home.name, reason="you asked about it")

    resolution = router.resolve(
        None,
        world=WorldStateSnapshot(active_device_id=phone.id),
        origin_device_id=phone.id,
        focus=focus.current(session),
    )
    assert resolution.device is phone


# ------------------------------------------------------------------ follow-me


def test_follow_me_answers_where_the_owner_is_not_where_the_work_ran(two_devices):
    _, phone, router = two_devices
    out = router.follow_me(
        world=WorldStateSnapshot(active_device_id=phone.id), origin_device_id=phone.id
    )
    assert out is phone


def test_follow_me_falls_back_to_the_most_recently_seen_device():
    """No anchor at all — the last machine anyone touched is the best evidence there is."""
    old = device("Server", seen=datetime.now(UTC) - timedelta(hours=3))
    recent = device("Kitchen-Tablet", seen=datetime.now(UTC) - timedelta(minutes=2))
    router = DeviceRouter(StubHub(old, recent))

    assert router.follow_me(world=WorldStateSnapshot()) is recent


def test_follow_me_skips_a_device_that_cannot_speak():
    headless = DeviceSummary(
        id=new_id(),
        name="Server",
        kind="server",
        os="Linux",
        status=DeviceStatus.ONLINE,
        capabilities=DeviceCapabilities.of("file", "shell"),  # no audio
    )
    speaker = device("Pixel", kind="phone")
    router = DeviceRouter(StubHub(headless, speaker))

    assert router.follow_me(world=WorldStateSnapshot()) is speaker


# ------------------------------------------------------------------ the remote gate


@pytest.fixture
def gate() -> RemoteCommandGate:
    return RemoteCommandGate()


def test_a_local_command_is_not_gated(gate):
    """This gate only exists for instructions that cross machines."""
    pc = device("Office-PC")
    verdict = gate.check(action="app.open", origin=pc, target=pc, origin_device_id=pc.id)
    assert verdict.allowed and not verdict.remote


def test_an_untrusted_device_cannot_drive_another(gate):
    phone = device("Pixel", kind="phone", trust=TrustLevel.LIMITED)
    pc = device("Home-PC", trust=TrustLevel.TRUSTED)
    verdict = gate.check(action="app.open", origin=phone, target=pc, origin_device_id=phone.id)
    assert not verdict.allowed
    assert "not trusted" in verdict.reason


def test_a_trusted_device_may_drive_another(gate):
    phone = device("Pixel", kind="phone", trust=TrustLevel.TRUSTED)
    pc = device("Home-PC")
    assert gate.check(action="app.open", origin=phone, target=pc, origin_device_id=phone.id)


def test_an_unrecognised_origin_is_refused(gate):
    """Claiming to come from somewhere unidentifiable is worse than claiming nothing."""
    pc = device("Home-PC")
    verdict = gate.check(action="app.open", origin=None, target=pc, origin_device_id=new_id())
    assert not verdict.allowed
    assert "not one I recognise" in verdict.reason


def test_no_origin_at_all_is_local_not_trusted(gate):
    """An automation or a direct API call. No second machine, so nothing to protect."""
    pc = device("Home-PC")
    verdict = gate.check(action="app.open", origin=None, target=pc, origin_device_id=None)
    assert verdict.allowed and not verdict.remote


def test_commands_are_not_relayed_over_an_unencrypted_link(gate):
    phone = device("Pixel", kind="phone", trust=TrustLevel.TRUSTED, encrypted=False)
    pc = device("Home-PC")
    verdict = gate.check(action="app.open", origin=phone, target=pc, origin_device_id=phone.id)
    assert not verdict.allowed
    assert "not encrypted" in verdict.reason


def test_an_untrusted_target_receives_nothing(gate):
    """Quarantine has to work in the receiving direction too, or it is not quarantine."""
    phone = device("Pixel", kind="phone", trust=TrustLevel.PRIMARY)
    quarantined = device("Old-Laptop", trust=TrustLevel.UNTRUSTED)
    verdict = gate.check(
        action="app.open", origin=phone, target=quarantined, origin_device_id=phone.id
    )
    assert not verdict.allowed


def test_every_action_that_writes_needs_remote_confirmation():
    """Derived from the level each action declares, not from a list someone maintains.

    Asserted over the whole catalogue rather than a handful of names, because the failure
    this guards against is *omission* — and a test that names five actions cannot catch a
    sixth going unlisted. An earlier hand-written list missed `file.copy`, `file.rename`,
    `file.create`, `clipboard.write` and `app.close`.
    """
    writes = [name for name, spec in CATALOGUE.items() if spec.level >= PermissionLevel.MODIFY]
    assert writes  # the assertion below is worthless if this is empty
    unguarded = [
        name
        for name in writes
        if not needs_confirmation_when_remote(name, level=CATALOGUE[name].level)
    ]
    assert unguarded == []


def test_reading_a_machine_does_not_raise_a_prompt():
    """ "Is my home PC on?" must not need approving.

    Prompts the owner learns to dismiss without reading are how a real prompt comes to be
    dismissed too. An over-broad rule here makes the system less safe, not more.
    """
    for name, spec in CATALOGUE.items():
        if spec.level is PermissionLevel.READ:
            assert not needs_confirmation_when_remote(name, level=spec.level), name


def test_starting_a_process_is_confirmed_despite_being_below_the_line():
    """Below MODIFY, and still not the same as opening a window on your own machine."""
    assert needs_confirmation_when_remote("system.process.start", level=PermissionLevel.OPEN)


def test_opening_an_app_remotely_is_not_confirmed():
    """The V8 acceptance flow itself. If this needed approving, the feature would not work."""
    assert not needs_confirmation_when_remote("app.open", level=PermissionLevel.OPEN)


# ------------------------------------------------------------------ permission escalation


@pytest.fixture
def permissions() -> PermissionEngine:
    return PermissionEngine()


def test_a_remote_delete_is_asked_every_time(permissions):
    """And ASK_ALWAYS, not ASK_ONCE — a standing grant is exactly what must not exist."""
    origin, target = new_id(), new_id()
    verdict = permissions.decide(
        ActionRequest(
            action="file.delete",
            resource="/home/x/report.xlsx",
            device_id=target,
            origin_device_id=origin,
            level=PermissionLevel.MODIFY,
        )
    )
    assert verdict.decision is PolicyDecision.ASK_ALWAYS


def test_a_remote_command_cannot_ride_an_existing_grant(permissions):
    """A grant is a decision the owner made in one situation, not a capability."""
    origin, target = new_id(), new_id()
    # `system.process.stop` is ASK_ONCE, so it is one of the few consequential actions that
    # *can* become a standing grant — which makes it the case worth testing.
    permissions.add_grant(PermissionGrant(action="system.process.stop", device_id=target))

    local = permissions.decide(
        ActionRequest(action="system.process.stop", resource="chrome", device_id=target)
    )
    assert local.decision is PolicyDecision.AUTO
    assert local.rule == "standing_grant"

    remote = permissions.decide(
        ActionRequest(
            action="system.process.stop",
            resource="chrome",
            device_id=target,
            origin_device_id=origin,
        )
    )
    assert remote.decision is PolicyDecision.ASK_ALWAYS
    assert remote.rule == "remote_command"


# ------------------------------------------------------------------ parsing


@pytest.mark.parametrize(
    ("said", "app"),
    [
        ("เปิด Chrome ให้หน่อย", "chrome"),
        ("เปิด chrome ให้หน่อยครับ", "chrome"),
        ("เปิด notepad ครับ", "notepad"),
        ("open chrome please", "chrome"),
    ],
)
def test_politeness_is_not_part_of_the_application_name(said, app):
    """ "เปิด Chrome ให้หน่อย" asks for Chrome, not for an app called "chrome ให้หน่อย"."""
    match = parse(said)
    assert match is not None
    assert match.intent.entities["app"] == app


def test_an_app_whose_name_is_a_word_survives():
    """LINE is a real application and a very common one here. It must not be trimmed."""
    match = parse("เปิด line")
    assert match is not None and match.intent.entities["app"] == "line"


@pytest.mark.parametrize("said", ["ผลเมื่อกี้เป็นยังไง", "how did that go", "what happened with that"])
def test_asking_after_finished_work_is_recognised(said):
    match = parse(said)
    assert match is not None
    assert match.intent.entities.get("subject") == "last_task"


def test_continuing_on_the_previous_machine_is_recognised():
    match = parse("ทำต่อจากเครื่องเมื่อกี้")
    assert match is not None
    assert match.intent.entities.get("continue") is True


# ------------------------------------------------------------------ deixis


async def test_that_file_still_resolves_from_world_state(container, session_id):
    """A regression guard for the *other* half of `_anchor`.

    The device half of that method was removed in V8 — it manufactured a "this machine"
    the owner never said, which is what stopped the conversation's focus from ever being
    consulted. The file half stayed, and had no test at all: the removal briefly took the
    surrounding line with it and nothing failed. It does now.
    """
    container.world.update(last_referenced_file="/home/x/report.xlsx")
    context = await container.context_engine.build(
        ConversationTurn(session_id=session_id, role="user", text="เปิดไฟล์นั้น")
    )
    intent = Intent(
        kind=IntentKind.FILE_ACTION,
        objective="open that file",
        entities={"action": "file.open", "path": "that file"},
    )

    anchored = container.reasoning._anchor(intent, context)
    assert anchored.entities["path"] == "/home/x/report.xlsx"


async def test_an_utterance_that_names_no_device_leaves_the_hint_empty(container, session_id):
    """The bug this replaced: a fabricated "this" read downstream as an explicit demand."""
    context = await container.context_engine.build(
        ConversationTurn(session_id=session_id, role="user", text="เปิด chrome")
    )
    intent = Intent(kind=IntentKind.COMPUTER_ACTION, objective="open chrome")

    assert container.reasoning._anchor(intent, context).target_device is None
