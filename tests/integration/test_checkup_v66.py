"""Check Thursday, and Repair Thursday (EASY INSTALL) — Sprint 66.

    Settings → Check Thursday  →  "Everything OK"
                              or  "Local AI ไม่ตอบสนอง — Repair"
    Button   → Repair Thursday →  restart services · reconnect local AI · re-register Node
                              but "ห้ามแก้ไข security-sensitive state โดยไม่มี confirmation"

The interesting tests here are not that a healthy system says it is healthy. They are the
three places this feature could quietly become a hole: a Repair button that reaches around
the Permission Engine, an internal name leaking into a sentence a normal user reads, and a
connection error standing in for "start the thing you configured".
"""

from __future__ import annotations

import inspect

import pytest
from httpx import ASGITransport, AsyncClient
from thursday_api.app import create_app
from thursday_core import checkup
from thursday_core.checkup import EVERYTHING_OK, UNKNOWN_COMPONENT, Checkup, Finding, describe
from thursday_core.config import Settings
from thursday_core.plain import leaks
from thursday_core.recovery import NEVER_AUTOMATIC, SELF_REPAIRS, is_self_repairable


class _Health:
    """A container as `check()` actually uses it: settings, health(), recovery."""

    def __init__(self, checks, *, settings=None, recovery=None):
        self._checks = checks
        self.settings = settings
        self.recovery = recovery

    async def health(self):
        return list(self._checks)


def _settings(tmp_path, **over) -> Settings:
    return Settings(
        data_dir=tmp_path / "var",
        obsidian_vault=tmp_path / "vault",
        database_url=f"sqlite+aiosqlite:///{tmp_path}/thursday.db",
        log_level="WARNING",
        llm_backend="rule",
        **over,
    )


# --------------------------------------------------------------- what the owner is told


async def test_a_working_thursday_says_so_in_one_line(tmp_path):
    report = await checkup.check(
        _Health(
            [{"component": "devices", "ok": True}, {"component": "database", "ok": True}],
            settings=_settings(tmp_path),
        )
    )
    assert report.ok
    assert report.headline() == EVERYTHING_OK
    assert report.render()["problems"] == []


async def test_a_broken_component_names_itself_in_the_owners_language_and_offers_repair(tmp_path):
    """The requirement's own example: a component, a plain name, and a next step."""
    report = await checkup.check(
        _Health(
            [{"component": "model:ollama:llama3.1:8b", "ok": False, "detail": "boom"}],
            settings=_settings(tmp_path),
        )
    )
    assert not report.ok
    assert report.headline() == "AI ในเครื่องไม่ตอบสนอง — ซ่อมได้"
    assert report.problems[0].repair == "switch_model"


@pytest.mark.parametrize(
    ("component", "expected"),
    [
        ("model:ollama:llama3.1:8b", "AI ในเครื่อง"),
        ("model:rule-based", "AI ในเครื่อง"),
        ("model:anthropic:claude-sonnet-4-5", "AI บนคลาวด์"),
        ("model:openai:gpt-4o", "AI บนคลาวด์"),
        ("model:some-future-provider:x", "AI"),
    ],
)
def test_a_model_failure_says_where_the_model_actually_runs(component, expected):
    """The requirement's example is "Local AI ไม่ตอบสนอง", so being wrong about *which* AI
    makes the sentence worse than saying nothing.

    The first version asked whether "cloud" appeared in the component name. No provider is
    called that — they are `rule-based`, `ollama:…`, `anthropic:…` — so a cloud outage told
    the owner the AI on their own machine had stopped. Runtimes are declared now, and a
    provider on neither list gets the label that is merely vague rather than wrong.
    """
    assert describe(component)[0] == expected


def test_every_declared_runtime_is_on_exactly_one_side():
    assert not (checkup.LOCAL_RUNTIMES & checkup.CLOUD_RUNTIMES)


def test_the_providers_this_build_actually_ships_are_all_classified():
    """A provider added to `llm.py` without a row here is described as "AI" — vague but true.
    This is the test that says so out loud, against the real provider names."""
    import thursday_models.llm as llm

    shipped = {llm.RuleBasedLLM.name, llm.MockLLM.name, "ollama", "anthropic"}
    assert shipped <= (checkup.LOCAL_RUNTIMES | checkup.CLOUD_RUNTIMES)


async def test_the_real_container_can_be_checked(container):
    """Against the container the rest of the suite uses, not a stand-in for one.

    `check()` reads `health()`, `settings` and `recovery`; a fake proves the translation and
    only the real thing proves those three attributes are there and are what it thinks.
    """
    report = await checkup.check(container)
    assert report.render()["checked"] == len(await container.health())
    assert isinstance(report.headline(), str) and report.headline()


async def test_every_component_health_reports_has_a_translation(container):
    """The allowlist has to actually cover the list. Otherwise it degrades quietly: a
    component added to `health()` shows up as "ส่วนประกอบภายใน", which is safe and useless,
    and nothing anywhere says so."""
    emitted = {str(c["component"]) for c in await container.health()}
    untranslated = {
        c for c in emitted if not c.startswith("model:") and c not in checkup.COMPONENTS
    }
    assert untranslated == set()


async def test_the_translation_table_has_no_rows_for_things_that_do_not_exist(container):
    """The other direction, and the one this project keeps getting wrong. The first version
    of this table translated `vision` and `models` — neither of which `health()` has ever
    reported — while missing four components it reports every time."""
    emitted = {str(c["component"]) for c in await container.health()}
    assert set(checkup.COMPONENTS) <= emitted


# ------------------------------------------------------------------- the security boundary


@pytest.mark.parametrize("forbidden", sorted(NEVER_AUTOMATIC))
async def test_a_security_sensitive_repair_is_refused_and_reported_as_needing_a_person(
    container, forbidden
):
    """ "ห้ามแก้ไข security-sensitive state โดยไม่มี confirmation", and stronger than that.

    Every one of these is refused outright rather than confirmed, because each *changes what
    Thursday is permitted to do* rather than restoring what it could already do. The refusal
    happens in `SelfRecovery`, so posting one here is declined in the same words whether it
    came from the owner, a model, or a page that persuaded a browser to post it.
    """
    result = await checkup.repair(container, "devices", forbidden)
    assert result["ok"] is False
    assert result["attempted"] is False
    assert result["needs_a_person"] is True
    assert "ทำเองไม่ได้" in result["message"]


async def test_no_repair_button_is_ever_offered_for_something_recovery_would_refuse(tmp_path):
    """The button and the boundary cannot disagree, because they are the same predicate.

    A `COMPONENTS` row is one edit away from naming `rotate_credential` as the obvious fix
    for a credential problem. This is the test that would catch it.
    """
    for component, (_, repair) in checkup.COMPONENTS.items():
        if repair is not None:
            assert is_self_repairable(repair), f"{component} offers a repair nobody may run"
            assert repair in SELF_REPAIRS
    for label, repair in (checkup._LOCAL_MODEL, checkup._CLOUD_MODEL):
        assert is_self_repairable(repair), f"{label} offers a repair nobody may run"


async def test_a_repair_is_only_offered_when_the_thing_is_actually_broken(tmp_path):
    """A healthy component carries no button. Otherwise the settings screen is a row of
    buttons that do nothing, and the one that matters is lost among them."""
    report = await checkup.check(
        _Health([{"component": "devices", "ok": True}], settings=_settings(tmp_path))
    )
    assert report.findings[0].repair is None


async def test_repair_has_no_way_to_skip_the_recovery_layer(container):
    """No `force`, no `confirm`, no direct callable — the signature is the whole argument.

    V10 built one door. A second one here, however well-meant, would be the one that gets
    used in a crisis and the one nobody reviews.
    """
    parameters = inspect.signature(checkup.repair).parameters
    assert set(parameters) == {"container", "component", "action"}

    source = inspect.getsource(checkup.repair)
    assert "recovery.repair(" in source
    for forbidden in NEVER_AUTOMATIC:
        assert forbidden not in source


async def test_an_unregistered_but_allowed_repair_is_reported_honestly(container):
    """`clear_cache` is on the allowlist and this container wires no handler for it. That is
    "no repair is wired up", not a success and not a refusal — §194's rule that nothing is
    marked done without verification applies to repairs too."""
    result = await checkup.repair(container, "redis", "clear_cache")
    assert result["ok"] is False
    assert result["attempted"] is False


async def test_a_repair_that_ran_but_fixed_nothing_does_not_report_success(container):
    """§194: "No task marked success without verification." It applies to repairs too.

    The container wires `reconnect_node` to a placeholder that does nothing. The first
    version of this module ran it, saw no exception, and told the owner
    "ซ่อมการเชื่อมต่อกับเครื่องเรียบร้อย" — about a machine in exactly the state it was in.
    `ok` follows the health check now, not the handler's return.
    """
    assert not container.hub.online()  # nothing connected, so `devices` is genuinely down

    result = await checkup.repair(container, "devices", "reconnect_node")

    assert result["attempted"] is True  # the handler did run
    assert result["verified"] is False  # and it changed nothing
    assert result["ok"] is False
    assert "ยังไม่กลับมาทำงาน" in result["message"]


async def test_a_repair_that_actually_works_is_reported_as_done(container, office_pc):
    """The other half: a real fix, verified by the same check that judged it broken."""
    ran = []
    container.recovery.register("clear_cache", lambda: ran.append(1))

    result = await checkup.repair(container, "devices", "clear_cache")

    assert ran == [1]
    assert result["attempted"] is True
    assert result["verified"] is True
    assert result["ok"] is True
    assert result["needs_a_person"] is False


async def test_a_repair_nothing_reports_on_says_so_rather_than_claiming_success(container):
    """No health check covers `queue`, so there is no observation to derive an answer from.
    "ตรวจสอบผลไม่ได้" is the honest outcome; the one that costs an afternoon is "เรียบร้อย"."""
    container.recovery.register("restart_worker", lambda: None)

    # No health check reports on `obsidian` — a stale component name from an older client is
    # exactly how this arrives in practice.
    result = await checkup.repair(container, "obsidian", "restart_worker")

    assert result["attempted"] is True
    assert result["verified"] is None
    assert result["ok"] is False
    assert "ตรวจสอบผลไม่ได้" in result["message"]


async def test_giving_up_reads_differently_from_refusing(container):
    """Three reasons a repair did not happen, and the owner should be able to tell them
    apart: never allowed, out of attempts, or nothing wired up."""
    container.recovery.register("clear_cache", lambda: None)
    for _ in range(4):
        await checkup.repair(container, "redis", "clear_cache")

    exhausted = await checkup.repair(container, "redis", "clear_cache")
    assert exhausted["message"] == checkup.GAVE_UP

    refused = await checkup.repair(container, "redis", "change_security")
    assert refused["message"] == checkup.NEEDS_A_PERSON

    unwired = await checkup.repair(container, "memory", "retry_request")
    assert unwired["message"] == checkup.NO_REPAIR


async def test_the_reason_shown_to_the_owner_is_declared_not_interpolated(container):
    """`SelfRecovery`'s reason is written for an operator and names the raw action. It is
    kept in `technical`; the sentence a person reads comes from the table above it."""
    result = await checkup.repair(container, "devices", "grant_access")
    assert result["message"] == checkup.NEEDS_A_PERSON
    assert "grant_access" not in result["message"]
    assert "grant_access" in result["technical"]


# ------------------------------------------------------------------ nothing internal leaks


async def test_an_unrecognised_component_becomes_vague_rather_than_raw(tmp_path):
    """Sprint 65's allowlist rule, applied to component names. A component added later shows
    up as "ส่วนประกอบภายใน" until somebody translates it — less informative, and never a
    leaked internal in front of somebody who did not ask for one."""
    label, repair = describe("kafka_consumer_group_lag")
    assert label == UNKNOWN_COMPONENT
    assert repair is None

    report = await checkup.check(
        _Health(
            [{"component": "kafka_consumer_group_lag", "ok": False, "detail": "ECONNREFUSED"}],
            settings=_settings(tmp_path),
        )
    )
    assert "kafka" not in str(report.render()).lower()


async def test_nothing_a_normal_user_reads_carries_an_internal(container):
    """The whole rendered screen, against the same forbidden-term list Sprint 65 uses.

    `health()` writes a masked DSN, a provider id and raw connection errors into `detail`.
    None of that reaches this dict, because `technical` is opt-in rather than filtered.
    """
    report = await checkup.check(container)
    rendered = report.render()
    assert leaks(str(rendered)) == []
    assert all("technical" not in row for row in rendered["problems"])


async def test_developer_options_can_see_everything_that_was_hidden(tmp_path):
    """The detail is kept, not destroyed — it is just behind a flag nothing else turns on."""
    report = await checkup.check(
        _Health(
            [{"component": "redis", "ok": False, "detail": "ECONNREFUSED 127.0.0.1:6379"}],
            settings=_settings(tmp_path),
        )
    )
    plain = report.render()
    advanced = report.render(advanced=True)
    assert leaks(str(plain)) == []
    assert advanced["problems"][0]["technical"] == "ECONNREFUSED 127.0.0.1:6379"
    assert advanced["problems"][0]["component"] == "redis"


# ----------------------------------------------------------------- services, not tracebacks


async def test_a_desktop_install_has_no_service_to_name(tmp_path):
    """Why the product names below are not a hole in the rule: on the edition a normal user
    installs, this list is empty by construction. SQLite and an in-process cache."""
    settings = _settings(tmp_path)
    assert settings.is_desktop
    assert settings.external_services() == []

    report = await checkup.check(_Health([{"component": "devices", "ok": True}], settings=settings))
    assert report.missing_services == []
    assert leaks(str(report.render())) == []


async def test_a_configured_service_is_named_rather_than_left_to_a_connection_error(tmp_path):
    """Sprint 62's point. Somebody set `REDIS_URL` by hand; that person is the reader, and
    "ต้องเปิด Redis ก่อน" is what they need instead of an errno."""
    settings = _settings(tmp_path, edition="hub", redis_url="redis://localhost:6379/0")
    report = await checkup.check(
        _Health([{"component": "redis", "ok": False, "detail": "ECONNREFUSED"}], settings=settings)
    )
    assert report.missing_services == ["Redis"]
    assert report.headline() == "ต้องเปิด Redis ก่อน"
    assert not report.ok


async def test_a_missing_service_makes_the_report_not_ok_even_if_every_check_passed(tmp_path):
    """The failure this guards against is a checkup that says "ทุกอย่างปกติ" on a machine
    where nothing has been started yet, because no check has failed *yet*."""
    settings = _settings(tmp_path, edition="hub", redis_url="redis://localhost:6379/0")
    report = await checkup.check(_Health([{"component": "devices", "ok": True}], settings=settings))
    assert not report.ok
    assert report.headline() != EVERYTHING_OK


# ------------------------------------------------------------------------------- endpoints


@pytest.fixture
async def client(settings, container, office_pc):
    app = create_app(settings, container=container)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://thursday.test"
    ) as http:
        app.state.container = container
        yield http


async def test_the_two_endpoints_are_the_two_buttons(client):
    response = await client.get("/api/v1/checkup")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"ok", "headline", "problems", "checked", "missing_services"}
    assert leaks(str(body)) == []

    refused = await client.post(
        "/api/v1/repair", params={"component": "devices", "action": "grant_access"}
    )
    assert refused.status_code == 200
    assert refused.json()["needs_a_person"] is True
    assert refused.json()["attempted"] is False


async def test_a_failing_repair_does_not_hand_the_owner_the_exception_it_raised(client, container):
    """The handler is arbitrary code and its exception text is arbitrary. A local model that
    is not running raises "ECONNREFUSED localhost:11434" verbatim, and that is a port number
    in front of somebody who never chose to run anything on one."""

    def boom():
        raise ConnectionError("ECONNREFUSED localhost:11434")

    container.recovery.register("restart_worker", boom)

    response = await client.post(
        "/api/v1/repair", params={"component": "queue", "action": "restart_worker"}
    )
    body = response.json()
    assert body["ok"] is False
    assert "technical" not in body
    assert leaks(str(body)) == []

    detailed = await client.post(
        "/api/v1/repair",
        params={"component": "queue", "action": "restart_worker", "advanced": True},
    )
    assert "ECONNREFUSED" in detailed.json()["technical"]


async def test_the_checkup_endpoint_and_the_health_endpoint_never_disagree(client):
    """Two views, one source. A second health check would be a second thing to keep true."""
    health = (await client.get("/api/v1/health")).json()
    checked = (await client.get("/api/v1/checkup")).json()
    assert checked["checked"] == len(health["checks"])
    assert checked["ok"] == (health["ok"] and not checked["missing_services"])


def test_a_finding_reports_its_own_repairability():
    assert Finding(component="devices", label="x", ok=False, repair="reconnect_node").repairable
    assert not Finding(component="devices", label="x", ok=False).repairable
    assert Checkup().ok
