"""Schedule triggers, which until now were stored and never fired (V15)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from thursday_automation.engine import AutomationEngine
from thursday_automation.rules import Action, Automation, Trigger
from thursday_core.bus import InProcessEventBus
from thursday_shared.enums import ProactivityLevel

#: 2026-09-07 07:30 in Bangkok is 00:30 UTC. Every time below is UTC, so a matcher that
#: forgot to convert would fail these rather than pass them by luck.
MONDAY_0730_BKK = datetime(2026, 9, 7, 0, 30, tzinfo=UTC)


def engine() -> AutomationEngine:
    return AutomationEngine(bus=InProcessEventBus(), timezone="Asia/Bangkok")


def weekday_rule(**overrides) -> Automation:
    return Automation(
        **{
            "name": "สรุปเช้า",
            "trigger": Trigger(kind="schedule", cron="30 7 * * 1-5"),
            "actions": [Action(kind="notify", args={"title": "สรุป"})],
            "enabled": True,
            **overrides,
        }
    )


async def test_a_schedule_fires_at_its_minute():
    e = engine()
    rule = e.add(weekday_rule())
    assert await e.due(MONDAY_0730_BKK) == [rule]


async def test_the_cron_is_read_in_the_owners_timezone_not_utc():
    """07:30 in `settings.timezone`. Evaluated in UTC this rule would run at 14:30 local
    and nothing would say so."""
    e = engine()
    e.add(weekday_rule())
    assert await e.due(MONDAY_0730_BKK)
    assert not await e.due(datetime(2026, 9, 7, 7, 30, tzinfo=UTC))


async def test_a_minute_is_served_once_however_often_the_sweep_runs():
    """The worker loop is not minute-aligned, so it will tick twice inside one minute."""
    e = engine()
    e.add(weekday_rule())
    assert len(await e.due(MONDAY_0730_BKK)) == 1
    assert await e.due(MONDAY_0730_BKK.replace(second=41)) == []


async def test_the_next_day_is_a_new_minute():
    e = engine()
    e.add(weekday_rule())
    assert await e.due(MONDAY_0730_BKK)
    assert await e.due(MONDAY_0730_BKK.replace(day=8))


async def test_a_disabled_rule_does_not_fire():
    e = engine()
    e.add(weekday_rule(enabled=False))
    assert await e.due(MONDAY_0730_BKK) == []


async def test_an_event_rule_is_not_swept():
    """Two paths to running a rule would run it twice."""
    e = engine()
    e.add(Automation(name="x", trigger=Trigger(kind="event", event_kind="*"), enabled=True))
    assert await e.due(MONDAY_0730_BKK) == []


async def test_a_rule_below_the_proactivity_floor_waits():
    e = engine()
    e.add(weekday_rule(proactivity_min=ProactivityLevel.HIGH))
    e.gate.level = ProactivityLevel.NORMAL
    assert await e.due(MONDAY_0730_BKK) == []
    e.gate.level = ProactivityLevel.HIGH
    assert await e.due(MONDAY_0730_BKK)


async def test_an_unreadable_cron_is_skipped_not_guessed():
    """Refusing at fire time is too late to tell anybody; running it on a guessed schedule
    is worse. It is skipped and logged, and the builder refuses to save one in the first
    place."""
    e = engine()
    e.add(weekday_rule(trigger=Trigger(kind="schedule", cron="ทุกเช้า")))
    assert await e.due(MONDAY_0730_BKK) == []


async def test_sweeping_actually_runs_it():
    bus = InProcessEventBus()
    e = AutomationEngine(bus=bus, timezone="Asia/Bangkok")
    rule = e.add(weekday_rule())
    fired = await e.sweep(MONDAY_0730_BKK)
    assert fired == [rule]
    kinds = [event.kind for event in bus.history()]
    assert "automation.triggered" in kinds and "notification.raised" in kinds
    assert rule.run_count == 1


async def test_a_sweep_with_nothing_due_publishes_nothing():
    bus = InProcessEventBus()
    e = AutomationEngine(bus=bus, timezone="Asia/Bangkok")
    e.add(weekday_rule())
    assert await e.sweep(datetime(2026, 9, 7, 1, 30, tzinfo=UTC)) == []
    assert bus.history() == []


@pytest.mark.parametrize("when", [datetime(2026, 9, 12, 0, 30, tzinfo=UTC)])
async def test_a_weekday_rule_stays_quiet_at_the_weekend(when):
    """12 Sep 2026 is a Saturday, 07:30 Bangkok."""
    e = engine()
    e.add(weekday_rule())
    assert await e.due(when) == []


# ------------------------------------------------- the job that makes it actually happen


async def test_the_worker_runs_a_schedule_sweep():
    """A `sweep()` nobody calls is the same silence in a different place."""
    from thursday_worker.jobs import BackgroundWorker, JobSchedule

    assert JobSchedule().schedule_sweep_s <= 60, "schedules are minute-granular"

    swept: list[bool] = []

    class Engine:
        async def sweep(self):
            swept.append(True)
            return []

    class Container:
        automations = Engine()

    await BackgroundWorker(Container()).sweep_schedules()
    assert swept == [True]


async def test_the_sweep_is_one_of_the_workers_started_loops():
    import asyncio

    from thursday_worker.jobs import BackgroundWorker

    class Engine:
        async def sweep(self):
            return []

    class Container:
        automations = Engine()

    worker = BackgroundWorker(Container())
    await worker.start()
    try:
        assert "schedules" in {task.get_name() for task in worker._tasks}
    finally:
        await worker.stop()
        await asyncio.sleep(0)


async def test_an_unknown_timezone_fails_at_startup_not_every_thirty_seconds():
    """A sweep that raises on every tick logs noise forever while schedules quietly never
    fire — the exact silence this sweep exists to end. So the setting is read once."""
    from thursday_automation.cron import CronRefused

    with pytest.raises(CronRefused, match="เขตเวลา"):
        AutomationEngine(bus=InProcessEventBus(), timezone="Mars/Olympus_Mons")


async def test_deleting_a_rule_forgets_that_it_fired():
    """Otherwise a recreated rule can inherit an 'already served this minute' it never
    earned, and a long-lived process keeps one entry per rule ever deleted."""
    e = engine()
    rule = e.add(weekday_rule())
    assert await e.due(MONDAY_0730_BKK) == [rule]

    e.remove(rule.id)
    again = e.add(weekday_rule(id=rule.id))
    assert await e.due(MONDAY_0730_BKK) == [again]
