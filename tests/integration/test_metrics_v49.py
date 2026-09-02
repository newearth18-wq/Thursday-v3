"""Metrics (§128–131, Sprint 49).

Two failure modes shape these tests, and neither is about the exposition format.

**A label is an egress path nobody classifies.** Every other way data leaves Thursday goes
through the privacy classifier or the redactor. Metrics do not: a monitoring system has none
of Thursday's controls, retains far longer, and is read by whoever runs the dashboard. So most
of what follows is about what *cannot* appear in a label.

**A metric that stopped being recorded reads as zero.** The same shape as the unmetered
prompts Sprint 45 found: the dashboard is green because nothing is reporting.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from thursday_api.app import create_app
from thursday_core.metrics import OTHER, MetricsCollector, MetricsRegistry, build_registry
from thursday_shared.enums import PermissionLevel, RiskLevel
from thursday_shared.models import ActionRequest, Event

PRIVATE = [
    "/home/owner/tax/2026-divorce-settlement.pdf",
    "C:\\Users\\owner\\Documents\\medical\\results.docx",
    "the owner's message to their lawyer",
    "sk-ant-api03-abcdefghijklmnopqrstuvwxyz012345",
]


@pytest.fixture
async def client(settings, container, office_pc):
    app = create_app(settings, container=container)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://thursday.test"
    ) as http:
        app.state.container = container
        yield http


# --------------------------------------------------------------------------- labels


def test_a_label_cannot_be_declared_without_a_bounded_set():
    """Refused at registration, not at record time. An unbounded label that exists and is
    merely never given a bad value is one line away from being given one."""
    registry = MetricsRegistry()
    with pytest.raises(ValueError, match="bounded set"):
        registry.register("thursday_thing_total", help="x", labels=("path",))


@pytest.mark.parametrize("value", PRIVATE)
def test_private_strings_collapse_rather_than_becoming_series(value):
    registry = build_registry()
    registry.inc("thursday_device_actions_total", action=value, outcome="verified")
    rendered = registry.render()

    assert value not in rendered
    assert f'action="{OTHER}"' in rendered


@pytest.mark.parametrize("value", PRIVATE)
def test_nothing_private_survives_the_whole_endpoint(value):
    """Belt and braces across every metric at once, because the risk is a *new* metric added
    later with a label somebody thought was safe."""
    registry = build_registry()
    for name in registry.names():
        for label in ("action", "outcome", "decision", "verdict", "agent", "pattern", "reason"):
            registry.inc(name, **{label: value})
    assert value not in registry.render()


def test_a_real_action_keeps_its_name(container):
    """The safety must not cost the signal: collapsing everything would be safe and useless."""
    container.metrics.inc("thursday_device_actions_total", action="file.delete", outcome="failed")
    assert 'action="file.delete"' in container.metrics.render()


def test_the_allowed_actions_come_from_the_catalogue_not_a_hand_written_list(container):
    """So a new action is measurable the day it exists. Written as a test because the first
    version of this caught `Exception` around the catalogue import and fell back to a
    one-element set — every action would have read `other` for ever, with the endpoint
    returning 200 and the dashboard drawing a line."""
    from thursday_devices.actions import CATALOGUE

    for action in list(CATALOGUE)[:5]:
        container.metrics.inc("thursday_device_actions_total", action=action, outcome="verified")
    rendered = container.metrics.render()
    for action in list(CATALOGUE)[:5]:
        assert f'action="{action}"' in rendered


def test_permission_metrics_record_the_decision_and_not_the_resource(container):
    """ "The owner was asked about ~/tax/2026-divorce.pdf" is a leak that looks like ordinary
    engineering."""
    container.permissions.decide(
        ActionRequest(
            action="file.delete",
            resource=PRIVATE[0],
            level=PermissionLevel.MODIFY,
            risk=RiskLevel.HIGH,
        )
    )
    rendered = container.metrics.render()
    assert "thursday_permission_decisions_total" in rendered
    assert PRIVATE[0] not in rendered
    assert 'decision="ASK_ALWAYS"' in rendered


# --------------------------------------------------------------------------- recording


def test_every_registered_series_is_exported_even_at_zero():
    """ "Nothing has gone wrong" and "the instrumentation broke" must not look identical, and
    on a dashboard a missing series and a flat zero are the two readings that matter most."""
    registry = build_registry()
    rendered = registry.render()
    for name in registry.names():
        assert f"# TYPE {name}" in rendered


def test_the_decision_counter_is_wired_through_the_one_place_decisions_are_made(container):
    """`_decide` has eighteen return paths. Counting at each is how one gets missed — the
    same mistake Sprint 45 found in cost accounting and Sprint 46 in redaction."""
    before = container.metrics.value("thursday_permission_decisions_total", decision="AUTO")
    for _ in range(3):
        container.permissions.decide(
            ActionRequest(action="clock.now", resource="", level=PermissionLevel.READ)
        )
    after = container.metrics.value("thursday_permission_decisions_total", decision="AUTO")
    assert after == before + 3


async def test_a_device_action_is_counted_through_the_bus(container):
    """The collector subscribes rather than the hub reporting: instrumentation each caller
    has to remember is instrumentation the important callers forget."""
    before = container.metrics.value(
        "thursday_device_actions_total", action="app.open", outcome="verified"
    )
    await container.bus.publish(
        Event(
            kind="device.action_completed",
            payload={"action": "app.open", "ok": True, "verified": True},
        )
    )
    assert (
        container.metrics.value(
            "thursday_device_actions_total", action="app.open", outcome="verified"
        )
        == before + 1
    )


async def test_an_unverified_action_is_counted_apart_from_a_verified_one(container):
    """The distinction the whole system rests on. A dashboard that merged them would hide
    exactly the failure ACT → VERIFY exists to catch."""
    for verified in (True, False):
        await container.bus.publish(
            Event(
                kind="device.action_completed",
                payload={"action": "app.open", "ok": True, "verified": verified},
            )
        )
    rendered = container.metrics.render()
    assert 'action="app.open",outcome="verified"' in rendered
    assert 'action="app.open",outcome="unverified"' in rendered


async def test_redactions_are_counted_by_pattern_and_never_by_value(container):
    """A metric that reported *what* it redacted has not redacted it."""
    from thursday_shared.models import LLMMessage, LLMRequest

    await container.models.complete(
        LLMRequest(messages=[LLMMessage(role="user", content=f"key {PRIVATE[3]}")])
    )
    rendered = container.metrics.render()
    assert 'pattern="anthropic_key"' in rendered
    assert PRIVATE[3] not in rendered


def test_an_unregistered_metric_is_refused_rather_than_created(container):
    """Creating on the fly is how an unbounded label gets in."""
    container.metrics.inc("thursday_invented_total", whatever="anything")
    assert "thursday_invented_total" not in container.metrics.render()


def test_a_histogram_reports_buckets_a_sum_and_a_count():
    registry = build_registry()
    for seconds in (0.1, 0.5, 3.0, 45.0):
        registry.observe("thursday_task_seconds", seconds)
    rendered = registry.render()
    assert 'thursday_task_seconds_bucket{le="+Inf"} 4' in rendered
    assert "thursday_task_seconds_count" in rendered
    assert "thursday_task_seconds_sum" in rendered


def test_gauges_are_read_at_scrape_time_not_mirrored(container):
    """A mirrored gauge is a second source of truth that can disagree with the first, and the
    disagreement is invisible."""
    assert container.metrics.value("thursday_devices_online") == len(container.hub.online())
    container.costs.record(provider="cloud", tier="FAST", usd=0.75)
    assert container.metrics.value("thursday_spend_today_usd") == pytest.approx(0.75)


# --------------------------------------------------------------------------- the endpoint


async def test_the_endpoint_serves_parseable_prometheus_text(client, container):
    container.metrics.inc("thursday_device_actions_total", action="app.open", outcome="verified")
    response = await client.get("/api/v1/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")

    body = response.text
    for line in body.splitlines():
        if line.startswith("#"):
            assert line.split()[1] in {"HELP", "TYPE"}
            continue
        name, _, value = line.rpartition(" ")
        assert name and value
        float(value)  # every sample is a number


async def test_the_endpoint_never_serves_a_path_or_a_secret(client, container):
    """The end-to-end version of the label rule, through the real app after real work."""
    await client.post("/api/v1/conversations", json={"text": "Thursday เปิด chrome"})
    container.permissions.decide(
        ActionRequest(
            action="file.delete",
            resource=PRIVATE[0],
            level=PermissionLevel.MODIFY,
            risk=RiskLevel.HIGH,
        )
    )
    body = (await client.get("/api/v1/metrics")).text
    for private in PRIVATE:
        assert private not in body
    assert "/home/" not in body
    assert "sk-ant" not in body


async def test_the_decisions_a_real_turn_makes_show_up(client, container):
    """If a conversation produces no metrics at all, the instrumentation is not attached —
    which is the failure this whole sprint is about."""
    await client.post("/api/v1/conversations", json={"text": "Thursday เปิด chrome"})
    body = (await client.get("/api/v1/metrics")).text
    decisions = [
        line
        for line in body.splitlines()
        if line.startswith("thursday_permission_decisions_total{")
    ]
    assert decisions, "a turn that opened an app made no permission decisions?"
    assert any(float(line.rsplit(" ", 1)[1]) > 0 for line in decisions)


def test_the_collector_reads_no_field_that_could_hold_content():
    """A payload is arbitrary. The collector may only look at keys that are outcomes."""
    import inspect

    source = inspect.getsource(MetricsCollector)
    for forbidden in ('"path"', '"resource"', '"content"', '"text"', '"args"', '"url"'):
        assert forbidden not in source, forbidden
