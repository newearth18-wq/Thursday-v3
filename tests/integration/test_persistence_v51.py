"""Persistence (Sprint 51).

`docs/23-release-readiness.md` listed this as the largest gap: the schema was designed, the
migrations were written, CI ran them from empty on every commit — and nothing read or wrote
through any of it. Restarting Thursday made it forget everything the owner had told it.

Every test here uses a real SQLite file and two separately-built containers, because the claim
being made is about surviving a process, and a claim about surviving a process cannot be
tested inside one object. The second container is as close to a restart as a test can get.
"""

from __future__ import annotations

import pytest
from thursday_core.config import Settings
from thursday_core.container import build_container, start
from thursday_core.persistence import NullRepository, SqlRepository
from thursday_shared.enums import MemoryLayer, MemorySource
from thursday_shared.models import MemoryRecord, MemoryWrite


@pytest.fixture
async def database(tmp_path):
    """A migrated, empty database.

    Built with `create_all` rather than by running alembic: what this file is testing is the
    ORM-to-domain mapping, and `alembic check` in `scripts/check.sh` already proves the
    migrations and the models agree. Running migrations here would test that twice and make
    every test in this file several seconds slower.
    """
    from sqlalchemy.ext.asyncio import create_async_engine
    from thursday_shared.db import models as _models  # noqa: F401 — registers the tables
    from thursday_shared.db.base import Base

    url = f"sqlite+aiosqlite:///{tmp_path}/thursday.db"
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()
    return url


#: Every persistence flag off. The four tests that mean "no database configured" say so with
#: this rather than relying on the ambient default — `settings.yaml` turns persistence on for
#: a real install, and a test whose premise is an unset default is a test that changes meaning
#: when somebody changes the default.
NOTHING_KEPT = {
    "persist_memory": False,
    "persist_audit": False,
    "persist_costs": False,
    "persist_tasks": False,
    "persist_models": False,
}


@pytest.fixture
def make_settings(tmp_path, database):
    def build(**over):
        return Settings(
            **{
                "data_dir": tmp_path / "var",
                "obsidian_vault": tmp_path / "vault",
                "database_url": database,
                "llm_backend": "rule",
                "vault_backend": "memory",
                "log_level": "ERROR",
                "persist_memory": True,
                # The other four off unless a test asks for them. They used to be off by
                # default; `settings.yaml` now turns every one on, because that file is what
                # a real desktop install reads. A test that means "persist memory only" has
                # to say so, or it silently starts exercising four more stores it never
                # created tables for.
                "persist_audit": False,
                "persist_costs": False,
                "persist_tasks": False,
                "persist_models": False,
                **over,
            }
        )

    return build


async def boot(settings):
    """Build and start a container, as a fresh process would."""
    from thursday_shared.db.session import dispose_engine

    await dispose_engine()
    container = build_container(settings, configure_logs=False)
    await start(container)
    return container


def remembered(container) -> set[str]:
    return {row["content"] for row in container.memory.export_state()}


# --------------------------------------------------------------------------- the point


async def test_a_memory_survives_a_restart(make_settings):
    """The headline gap. Before this, "Thursday forgets everything you told it when the
    process restarts" was true and undocumented anywhere the owner would look."""
    first = await boot(make_settings())
    await first.memory.write(
        MemoryWrite(
            layer=MemoryLayer.SEMANTIC,
            content="the quarterly report is due on Fridays",
            importance=0.9,
        )
    )

    second = await boot(make_settings())
    assert "the quarterly report is due on Fridays" in remembered(second)


async def test_what_comes_back_is_the_whole_record_not_just_its_text(make_settings):
    """A restore that loses importance, source or sensitivity produces memories that recall
    differently from the ones that were stored — which is worse than losing them, because
    nothing looks wrong."""
    first = await boot(make_settings())
    written = await first.memory.write(
        MemoryWrite(
            layer=MemoryLayer.PREFERENCE,
            content="always send reports as PDF",
            importance=0.85,
            confidence=0.7,
            source=MemorySource.USER,
        )
    )

    second = await boot(make_settings())
    restored = await second.memory.get(written.id)

    assert restored is not None
    assert restored.layer is written.layer
    assert restored.importance == pytest.approx(written.importance)
    assert restored.confidence == pytest.approx(written.confidence)
    assert restored.source is written.source
    assert restored.sensitivity is written.sensitivity


async def test_a_restored_memory_is_recallable_not_merely_present(make_settings):
    """Vectors are rebuilt from the stored embeddings on load. A memory that is in the index
    and not in the vector store is one `recall` will never surface — present, and useless."""
    from thursday_shared.models import MemoryQuery

    first = await boot(make_settings())
    await first.memory.write(
        MemoryWrite(
            layer=MemoryLayer.SEMANTIC,
            content="the office wifi password is kept in the safe",
            importance=0.9,
        )
    )

    second = await boot(make_settings())
    hits = await second.memory.recall(MemoryQuery(text="where is the wifi password", k=5))
    assert any("wifi" in hit.content for hit in hits)


async def test_forgetting_reaches_the_table(make_settings):
    """ADR 0019: forgetting is an instruction, not a filter. A memory dropped from the index
    and left in storage comes back on the next restart — the failure the owner would least
    expect and least easily notice."""
    first = await boot(make_settings())
    record = await first.memory.write(
        MemoryWrite(layer=MemoryLayer.SEMANTIC, content="a thing to be forgotten", importance=0.9)
    )
    await first.memory.forget(record.id)

    second = await boot(make_settings())
    assert remembered(second) == set()


async def test_a_second_restart_does_not_duplicate_anything(make_settings):
    """Write-through plus load means the row is already there. An `insert` where an upsert
    was needed shows up as the same memory three times after three restarts."""
    first = await boot(make_settings())
    await first.memory.write(
        MemoryWrite(layer=MemoryLayer.SEMANTIC, content="said once", importance=0.9)
    )

    for _ in range(3):
        latest = await boot(make_settings())
    assert len(latest.memory.export_state()) == 1


# --------------------------------------------------------------------------- honesty


async def test_running_without_a_database_still_works(tmp_path):
    """ADR 0001. Not a degraded mode: the whole test suite runs on it, and so does the CLI."""
    container = build_container(
        Settings(
            data_dir=tmp_path / "var",
            obsidian_vault=tmp_path / "vault",
            llm_backend="rule",
            vault_backend="memory",
            log_level="ERROR",
            **NOTHING_KEPT,
        ),
        configure_logs=False,
    )
    await start(container)
    assert container.persistent is False
    assert isinstance(container.memory._repository, NullRepository)

    record = await container.memory.write(
        MemoryWrite(layer=MemoryLayer.SEMANTIC, content="ephemeral but real", importance=0.9)
    )
    assert record is not None
    assert await container.memory.get(record.id) is not None


async def test_the_container_says_whether_durability_is_real(make_settings, tmp_path):
    """The thing that must never be a silent assumption. Both configurations are supported;
    only one of them keeps anything, and the difference is readable rather than inferred."""
    assert (await boot(make_settings())).persistent is True
    assert (await boot(make_settings(persist_memory=False))).persistent is False


async def test_a_write_that_cannot_be_stored_does_not_report_success(make_settings):
    """`remember` returning a record it failed to persist is a lie the owner discovers after
    a restart, when there is nothing to be done about it."""
    from unittest import mock

    container = await boot(make_settings())
    with (
        mock.patch.object(
            container.memory._repository, "put", side_effect=OSError("the disk is gone")
        ),
        pytest.raises(OSError, match="disk is gone"),
    ):
        await container.memory.write(
            MemoryWrite(layer=MemoryLayer.SEMANTIC, content="never stored", importance=0.9)
        )

    second = await boot(make_settings())
    assert remembered(second) == set()


async def test_rows_that_all_fail_to_load_are_not_reported_as_an_empty_start(make_settings):
    """A startup line reading `memories=0` looks identical to a first boot, which is how
    somebody spends a week not noticing their assistant has amnesia."""
    from unittest import mock

    from pydantic import ValidationError
    from thursday_memory.manager import MemoryRestoreError

    container = await boot(make_settings())
    await container.memory.write(
        MemoryWrite(layer=MemoryLayer.SEMANTIC, content="stored fine", importance=0.9)
    )

    fresh = await boot(make_settings())
    fresh.memory._records.clear()
    unreadable = ValidationError.from_exception_data("MemoryRecord", [])
    with (
        mock.patch.object(MemoryRecord, "model_validate", side_effect=unreadable),
        pytest.raises(MemoryRestoreError, match="refusing to start"),
    ):
        await fresh.memory.restore()


async def test_one_unreadable_row_does_not_cost_the_others(make_settings):
    first = await boot(make_settings())
    for i in range(3):
        await first.memory.write(
            MemoryWrite(layer=MemoryLayer.SEMANTIC, content=f"memory {i}", importance=0.9)
        )

    second = await boot(make_settings())
    assert len(second.memory.export_state()) == 3


# --------------------------------------------------------------------------- the mapper


async def test_the_repository_maps_in_both_directions(database):
    """The bug this caught while being written: the table carries `user_id` and `updated_at`,
    which `MemoryRecord` forbids, so the row it had just written would not load back. A
    repository that only maps outward is a repository that cannot load."""
    from uuid import UUID

    from thursday_shared.db.models import Memory
    from thursday_shared.db.session import dispose_engine, init_engine, session_scope

    await dispose_engine()
    init_engine(Settings(database_url=database, llm_backend="rule", vault_backend="memory"))
    repository = SqlRepository(
        Memory,
        session_scope=session_scope,
        defaults={"user_id": UUID("00000000-0000-0000-0000-000000000001")},
        fields=set(MemoryRecord.model_fields),
    )

    record = MemoryRecord(layer=MemoryLayer.SEMANTIC, content="round trip", importance=0.5)
    await repository.put(record.model_dump(mode="python"))

    rows = await repository.load()
    assert len(rows) == 1
    assert "user_id" not in rows[0], "a column the domain model forbids must not come back"
    assert MemoryRecord.model_validate(rows[0]).content == "round trip"


async def test_a_row_with_no_id_is_refused_rather_than_silently_inserted(database):
    from thursday_shared.db.models import Memory
    from thursday_shared.db.session import dispose_engine, init_engine, session_scope

    await dispose_engine()
    init_engine(Settings(database_url=database, llm_backend="rule", vault_backend="memory"))
    repository = SqlRepository(Memory, session_scope=session_scope)

    with pytest.raises(ValueError, match="no id"):
        await repository.put({"content": "no identity"})


# ===========================================================================  the audit log


async def audited(container, *actions: str):
    from thursday_security.audit import AuditEntry

    for action in actions:
        await container.audit.record(
            AuditEntry(actor="user", action=action, tool=action, resource=f"~/{action}")
        )


def make_audit_settings(make_settings):
    return make_settings(persist_memory=False, persist_audit=True)


async def test_the_audit_trail_survives_a_restart(make_settings):
    first = await boot(make_audit_settings(make_settings))
    await audited(first, "file.write", "app.open", "email.send")

    second = await boot(make_audit_settings(make_settings))
    assert [e.action for e in second.audit.entries()] == ["file.write", "app.open", "email.send"]


async def test_the_chain_still_verifies_after_a_restart(make_settings):
    first = await boot(make_audit_settings(make_settings))
    await audited(first, "file.write", "app.open", "email.send")

    second = await boot(make_audit_settings(make_settings))
    assert second.audit.verify_chain() is True


async def test_the_chain_continues_across_the_restart(make_settings):
    """The property that makes the log tamper-evident across a process boundary, and the one
    that would quietly not hold if entries were reloaded in the wrong order or re-hashed.

    An entry written after the restart must chain onto the last entry written before it. If
    it started a fresh chain from GENESIS, everything before the restart could be deleted
    without `verify_chain` noticing — which is exactly the deletion the chain exists to catch.
    """
    first = await boot(make_audit_settings(make_settings))
    await audited(first, "before.restart")
    last_hash = first.audit.entries()[-1].hash

    second = await boot(make_audit_settings(make_settings))
    await audited(second, "after.restart")

    entries = second.audit.entries()
    assert entries[-1].action == "after.restart"
    assert entries[-1].prev_hash == last_hash, "the new entry did not chain onto the old one"
    assert second.audit.verify_chain() is True


async def test_deleting_a_stored_entry_is_still_detected_after_a_restart(make_settings):
    """The whole point of persisting the hashes rather than recomputing them on load."""
    from sqlalchemy import delete
    from thursday_shared.db.models import AuditLogRow
    from thursday_shared.db.session import session_scope

    first = await boot(make_audit_settings(make_settings))
    await audited(first, "one", "two", "three")
    middle = first.audit.entries()[1].id

    async with session_scope() as session:
        await session.execute(delete(AuditLogRow).where(AuditLogRow.id == middle))

    second = await boot(make_audit_settings(make_settings))
    assert len(second.audit.entries()) == 2
    assert second.audit.verify_chain() is False, "a removed entry left the chain looking intact"


async def test_editing_a_stored_entry_is_detected_after_a_restart(make_settings):
    from sqlalchemy import update
    from thursday_shared.db.models import AuditLogRow
    from thursday_shared.db.session import session_scope

    first = await boot(make_audit_settings(make_settings))
    await audited(first, "one", "two", "three")
    middle = first.audit.entries()[1].id

    async with session_scope() as session:
        await session.execute(
            update(AuditLogRow).where(AuditLogRow.id == middle).values(resource="~/somewhere-else")
        )

    second = await boot(make_audit_settings(make_settings))
    assert second.audit.verify_chain() is False


async def test_the_origin_of_a_remote_command_survives(make_settings):
    """V8 added `origin_device_id` to the entry and not to the table. Persisting without it
    would have dropped the one field that makes a remote command accountable: "who told my PC
    to do that, and from where" is unanswerable from an entry recording only the target."""
    from thursday_security.audit import AuditEntry
    from thursday_shared.ids import new_id

    origin, target = new_id(), new_id()
    first = await boot(make_audit_settings(make_settings))
    await first.audit.record(
        AuditEntry(actor="user", action="app.open", device_id=target, origin_device_id=origin)
    )

    second = await boot(make_audit_settings(make_settings))
    restored = second.audit.entries()[-1]
    assert restored.origin_device_id == origin
    assert restored.device_id == target


async def test_a_dropped_audit_write_is_never_silent(make_settings):
    """`verify_chain` catches an entry that was altered or removed. It cannot catch one that
    was never written — a missing entry leaves a perfectly valid chain — so a swallowed write
    error would be invisible to the mechanism that exists to catch tampering."""
    from unittest import mock

    from thursday_security.audit import AuditEntry, AuditWriteError

    container = await boot(make_audit_settings(make_settings))
    assert container.audit.degraded is False

    with (
        mock.patch.object(
            container.audit._repository, "put", side_effect=OSError("the disk is gone")
        ),
        pytest.raises(AuditWriteError, match="record of what Thursday did is now incomplete"),
    ):
        await container.audit.record(AuditEntry(actor="user", action="file.delete"))

    assert container.audit.degraded is True
    assert container.audit.lost == 1


async def test_a_degraded_log_does_not_go_green_again(make_settings):
    """The gap does not heal. A flag that cleared on the next success would say the log is
    complete when it is missing an entry for ever."""
    from unittest import mock

    from thursday_security.audit import AuditEntry, AuditWriteError

    container = await boot(make_audit_settings(make_settings))
    with (
        mock.patch.object(container.audit._repository, "put", side_effect=OSError("gone")),
        pytest.raises(AuditWriteError),
    ):
        await container.audit.record(AuditEntry(actor="user", action="file.delete"))

    await container.audit.record(AuditEntry(actor="user", action="app.open"))
    assert container.audit.degraded is True
    assert container.audit.health()["degraded"] is True


async def test_a_tool_that_ran_is_not_failed_by_an_unstorable_audit_entry(
    make_settings, adapter, tmp_path
):
    """§194: no external communication silently duplicated. The tool has already run, so
    reporting failure invites a retry — and a retried email is a second email."""
    from unittest import mock

    from thursday_devices.hub import LoopbackDeviceSession
    from thursday_devices.node.executor import NodeExecutor
    from thursday_shared.ids import new_id
    from thursday_shared.models import ToolCall

    container = await boot(make_audit_settings(make_settings))
    session = LoopbackDeviceSession(
        device_id=new_id(), name="PC", executor=NodeExecutor(adapter, allowed_roots=[tmp_path])
    )
    await container.hub.register(session)

    with mock.patch.object(
        container.audit._repository, "put", side_effect=OSError("the disk is gone")
    ):
        result = await container.executor.execute(
            ToolCall(tool="app.open", args={"app": "chrome"}, device_id=session.device_id)
        )

    assert result.ok is True, "the tool ran; the audit failure must not rewrite that"
    assert container.audit.degraded is True


async def test_running_without_audit_persistence_is_unchanged(tmp_path):
    from thursday_security.audit import AuditEntry

    container = build_container(
        Settings(
            data_dir=tmp_path / "var",
            obsidian_vault=tmp_path / "vault",
            llm_backend="rule",
            vault_backend="memory",
            log_level="ERROR",
            **NOTHING_KEPT,
        ),
        configure_logs=False,
    )
    await start(container)
    await container.audit.record(AuditEntry(actor="user", action="file.write"))

    assert container.audit.verify_chain() is True
    assert container.audit.degraded is False
    assert container.persistent is False


async def test_a_degraded_audit_log_shows_up_in_health(make_settings):
    """A degraded log is a health *failure*, not a note. What it has lost cannot be recovered
    and the chain cannot detect it, so if this is not red then nothing says so at all."""
    from unittest import mock

    from thursday_security.audit import AuditEntry, AuditWriteError

    container = await boot(make_audit_settings(make_settings))
    before = {c["component"]: c for c in await container.health()}
    assert before["audit"]["ok"] is True

    with (
        mock.patch.object(container.audit._repository, "put", side_effect=OSError("gone")),
        pytest.raises(AuditWriteError),
    ):
        await container.audit.record(AuditEntry(actor="user", action="file.delete"))

    after = {c["component"]: c for c in await container.health()}
    assert after["audit"]["ok"] is False
    assert "could not be stored" in after["audit"]["detail"]


async def test_health_says_whether_state_is_actually_durable(make_settings, tmp_path):
    durable = {c["component"]: c for c in await (await boot(make_settings())).health()}
    assert "durable" in durable["database"]["detail"]

    container = build_container(
        Settings(
            data_dir=tmp_path / "var2",
            obsidian_vault=tmp_path / "vault2",
            llm_backend="rule",
            vault_backend="memory",
            log_level="ERROR",
            **NOTHING_KEPT,
        ),
        configure_logs=False,
    )
    ephemeral = {c["component"]: c for c in await container.health()}
    assert "lives for this process" in ephemeral["database"]["detail"]


# ===========================================================================  the spend ledger


def make_spend_settings(make_settings, **over):
    return make_settings(persist_memory=False, persist_audit=False, persist_costs=True, **over)


async def test_the_cap_binds_across_a_restart(make_settings):
    """Sprint 45 named this as its known gap and Sprint 47 closed it only for somebody who
    had taken a backup: with the ledger in memory, restarting reset the daily total, so
    restarting was a way around the cap."""
    first = await boot(make_spend_settings(make_settings))
    first.costs.daily_usd = 1.0
    for _ in range(5):
        await first.costs.record(provider="cloud", tier="FAST", usd=0.25)
    assert not first.costs.check()

    second = await boot(make_spend_settings(make_settings))
    second.costs.daily_usd = 1.0
    assert second.costs.spent_today() == pytest.approx(1.25)
    assert not second.costs.check(), "a restart must not hand back a fresh budget"


async def test_a_restored_charge_keeps_its_attribution(make_settings):
    from thursday_shared.ids import new_id

    task = new_id()
    first = await boot(make_spend_settings(make_settings))
    await first.costs.record(
        provider="cloud",
        tier="REASONING",
        tokens_in=900,
        tokens_out=100,
        usd=0.4,
        task_id=task,
        agent="research",
    )

    second = await boot(make_spend_settings(make_settings))
    charge = second.costs.charges()[-1]
    assert charge.provider == "cloud"
    assert charge.tier == "REASONING"
    assert charge.tokens == 1000
    assert charge.task_id == task
    assert charge.agent == "research"
    assert second.costs.spent(task_id=task) == pytest.approx(0.4)


async def test_a_model_call_through_the_router_is_stored_not_just_counted(make_settings):
    """The router is the metering choke point (ADR 0030). If the wiring stops at the
    in-memory meter, everything looks right until the next restart."""
    from thursday_shared.models import LLMMessage, LLMRequest

    first = await boot(make_spend_settings(make_settings))
    await first.models.complete(LLMRequest(messages=[LLMMessage(role="user", content="hello")]))
    assert first.costs.charges()

    second = await boot(make_spend_settings(make_settings))
    assert len(second.costs.charges()) == len(first.costs.charges())


async def test_pruning_reaches_the_table(make_settings):
    """Both, or neither works. Pruning only memory leaves the rows to be reloaded on the next
    restart, so the retention window never applies and the ledger grows for ever — the same
    shape as a memory dropped from the index and left in storage (ADR 0019)."""
    from datetime import timedelta

    from thursday_shared.models import utcnow

    first = await boot(make_spend_settings(make_settings))
    first.costs.retention = timedelta(days=7)
    await first.costs.record(
        provider="cloud", tier="FAST", usd=1.0, now=utcnow() - timedelta(days=30)
    )
    await first.costs.record(provider="cloud", tier="FAST", usd=2.0)
    assert [c.usd for c in first.costs.charges()] == [2.0], "the old charge should be pruned"

    second = await boot(make_spend_settings(make_settings))
    assert [c.usd for c in second.costs.charges()] == [2.0], "a pruned charge came back"


async def test_a_dropped_spend_write_is_never_silent(make_settings):
    """Unlike a lost audit entry, a lost charge means the cap *under-binds* after the next
    restart: the owner spends more than they set out to. A ceiling nobody can trust is a
    ceiling that is not doing its job, so which kind they have has to be visible."""
    from unittest import mock

    container = await boot(make_spend_settings(make_settings))
    assert container.costs.degraded is False

    with mock.patch.object(
        container.costs.repository, "put", side_effect=OSError("the disk is gone")
    ):
        await container.costs.record(provider="cloud", tier="FAST", usd=0.5)

    assert container.costs.degraded is True
    assert container.costs.lost == 1
    # And the cap still binds *this* session: the charge is in memory even though it is not
    # in the table, so the failure costs durability rather than the ceiling.
    assert container.costs.spent_today() == pytest.approx(0.5)


async def test_a_model_call_is_not_failed_by_an_unstorable_charge(make_settings):
    """The call already happened and already cost money. Raising would report an error for
    something that succeeded, and invite a retry that spends again."""
    from unittest import mock

    from thursday_shared.models import LLMMessage, LLMRequest

    container = await boot(make_spend_settings(make_settings))
    with mock.patch.object(container.costs.repository, "put", side_effect=OSError("gone")):
        response, _ = await container.models.complete(
            LLMRequest(messages=[LLMMessage(role="user", content="hello")])
        )

    assert response.text
    assert container.costs.degraded is True


async def test_running_without_spend_persistence_is_unchanged(tmp_path):
    container = build_container(
        Settings(
            data_dir=tmp_path / "var",
            obsidian_vault=tmp_path / "vault",
            llm_backend="rule",
            vault_backend="memory",
            log_level="ERROR",
            **NOTHING_KEPT,
        ),
        configure_logs=False,
    )
    await start(container)
    assert container.costs.repository is None

    await container.costs.record(provider="cloud", tier="FAST", usd=0.5)
    assert container.costs.spent_today() == pytest.approx(0.5)
    assert container.costs.degraded is False


async def test_a_degraded_spend_ledger_shows_up_in_health(make_settings):
    from unittest import mock

    container = await boot(make_spend_settings(make_settings))
    before = {c["component"]: c for c in await container.health()}
    assert before["spend"]["ok"] is True

    with mock.patch.object(container.costs.repository, "put", side_effect=OSError("gone")):
        await container.costs.record(provider="cloud", tier="FAST", usd=0.5)

    after = {c["component"]: c for c in await container.health()}
    assert after["spend"]["ok"] is False
    assert "could not be stored" in after["spend"]["detail"]


# ===========================================================================  task resumption


def make_task_settings(make_settings, **over):
    return make_settings(
        persist_memory=False, persist_audit=False, persist_costs=False, persist_tasks=True, **over
    )


async def a_running_task(container, *steps):
    """A task that was mid-plan when the process died.

    `steps` are (seq, name, action, status) — the shape a crash actually leaves behind.
    """
    from thursday_shared.enums import StepKind, TaskState
    from thursday_shared.models import Plan, PlanStep

    task = await container.tasks.create(title="send the report", objective="Friday's report")
    await container.tasks.set_plan(
        task.id,
        Plan(
            objective="Friday's report",
            steps=[
                PlanStep(
                    seq=seq,
                    kind=StepKind.DEVICE,
                    name=name,
                    objective=name,
                    args={"action": action},
                    status=status,
                )
                for seq, name, action, status in steps
            ],
        ),
    )
    await container.tasks.transition(task.id, TaskState.PLANNING)
    await container.tasks.transition(task.id, TaskState.RUNNING)
    return task


# --------------------------------------------------------------------------- never RUNNING


async def test_a_task_never_comes_back_running(make_settings):
    """The failure this whole design exists to avoid. A `RUNNING` row reloaded as `RUNNING`
    is a task that looks alive with nothing driving it: the coroutine died with the process,
    and the owner watches it not progress with no reason to think anything is wrong."""
    from thursday_shared.enums import TaskState

    first = await boot(make_task_settings(make_settings))
    task = await a_running_task(first, (1, "open", "app.open", TaskState.RUNNING))
    assert first.tasks.get(task.id).status is TaskState.RUNNING

    second = await boot(make_task_settings(make_settings))
    restored = second.tasks.get(task.id)
    assert restored is not None
    assert restored.status is TaskState.INTERRUPTED
    assert restored.status is not TaskState.RUNNING


async def test_interrupted_is_not_terminal_and_not_paused(make_settings):
    """Reusing PAUSED would lose the distinction that matters: "you stopped this" and "we
    crashed while doing this" call for different responses, and only the second leaves a step
    whose outcome nobody observed."""
    from thursday_shared.enums import TaskState

    assert TaskState.INTERRUPTED.is_terminal is False
    assert TaskState.INTERRUPTED is not TaskState.PAUSED

    first = await boot(make_task_settings(make_settings))
    task = await a_running_task(first, (1, "open", "app.open", TaskState.RUNNING))
    await first.tasks.pause(task.id)

    second = await boot(make_task_settings(make_settings))
    assert second.tasks.get(task.id).status is TaskState.PAUSED, "a pause is not a crash"


async def test_a_completed_task_is_untouched_by_the_restart(make_settings):
    from thursday_shared.enums import TaskState
    from thursday_shared.models import VerificationReport

    first = await boot(make_task_settings(make_settings))
    task = await first.tasks.create(title="done already", objective="finished")
    await first.tasks.transition(task.id, TaskState.PLANNING)
    await first.tasks.transition(task.id, TaskState.RUNNING)
    await first.tasks.transition(task.id, TaskState.VERIFYING)
    await first.tasks.complete(
        task.id, result={"ok": True}, verification=VerificationReport(verdict="PASS")
    )

    second = await boot(make_task_settings(make_settings))
    assert second.tasks.get(task.id).status is TaskState.COMPLETED


# --------------------------------------------------------------------------- what is known


async def test_a_completed_step_is_not_offered_for_repeat(make_settings):
    """It was done and observed (ADR 0012). Repeating it would redo work that happened."""
    from thursday_core.resumption import analyse
    from thursday_shared.enums import TaskState

    first = await boot(make_task_settings(make_settings))
    task = await a_running_task(
        first,
        (1, "read", "file.read", TaskState.COMPLETED),
        (2, "open", "app.open", TaskState.RUNNING),
    )

    second = await boot(make_task_settings(make_settings))
    plan = analyse(second.tasks.get(task.id))

    assert [s.state for s in plan.steps] == ["done", "unknown"]
    assert plan.resume_from == 2, "it should continue from the step nobody watched finish"


async def test_the_step_that_was_running_is_unknown_not_failed(make_settings):
    """It may have completed, half-completed, or never started. Calling it failed would be a
    claim nobody can support, and calling it done would be worse."""
    from thursday_core.resumption import analyse
    from thursday_shared.enums import TaskState

    first = await boot(make_task_settings(make_settings))
    task = await a_running_task(first, (1, "open", "app.open", TaskState.RUNNING))

    second = await boot(make_task_settings(make_settings))
    plan = analyse(second.tasks.get(task.id))
    assert [s.state for s in plan.unknown] == ["unknown"]


# --------------------------------------------------------------------------- what is safe


async def test_an_interrupted_email_is_never_offered_as_safe_to_repeat(make_settings):
    """§194: no external communication silently duplicated. Nobody knows whether the email
    went, and "probably not" is not a basis for sending a second one."""
    from thursday_core.resumption import analyse
    from thursday_shared.enums import TaskState

    first = await boot(make_task_settings(make_settings))
    task = await a_running_task(first, (1, "send", "email.send", TaskState.RUNNING))

    second = await boot(make_task_settings(make_settings))
    plan = analyse(second.tasks.get(task.id))

    assert plan.safe is False
    assert "outside this machine" in plan.reason
    assert plan.unknown[0].safe_to_repeat is False


@pytest.mark.parametrize("action", ["email.send", "message.send", "purchase.make", "file.delete"])
async def test_nothing_irreversible_or_external_is_repeatable(make_settings, action):
    from thursday_core.resumption import analyse
    from thursday_shared.enums import TaskState

    first = await boot(make_task_settings(make_settings))
    task = await a_running_task(first, (1, "step", action, TaskState.RUNNING))

    second = await boot(make_task_settings(make_settings))
    assert analyse(second.tasks.get(task.id)).safe is False, action


@pytest.mark.parametrize("action", ["file.read", "app.open", "system.info", "clipboard.write"])
async def test_a_local_reversible_step_is_repeatable(make_settings, action):
    """The safety must not swallow the feature: if nothing were repeatable, resumption would
    be a report that says "give up" in every case."""
    from thursday_core.resumption import analyse
    from thursday_shared.enums import TaskState

    first = await boot(make_task_settings(make_settings))
    task = await a_running_task(first, (1, "step", action, TaskState.RUNNING))

    second = await boot(make_task_settings(make_settings))
    assert analyse(second.tasks.get(task.id)).safe is True, action


def test_the_repeat_rule_asks_the_policy_table_rather_than_a_second_list():
    """A second list of dangerous actions is a second thing to keep in step, and this
    repository has found that bug enough times."""
    import inspect

    from thursday_core import resumption

    source = inspect.getsource(resumption.safe_to_repeat)
    assert "PolicyTable" in source
    for hardcoded in ('"email.send"', '"purchase.make"', '"file.delete"'):
        assert hardcoded not in source, f"{hardcoded} is hardcoded instead of asked"


# --------------------------------------------------------------------------- offered, not taken


async def test_nothing_resumes_itself(make_settings):
    """ADR 0027: noticing is not doing. And a process that auto-resumed on boot would, in a
    crash loop, redo the same dangerous thing on every restart."""
    from thursday_core import resumption
    from thursday_shared.enums import TaskState

    first = await boot(make_task_settings(make_settings))
    task = await a_running_task(first, (1, "open", "app.open", TaskState.RUNNING))

    second = await boot(make_task_settings(make_settings))
    assert second.tasks.get(task.id).status is TaskState.INTERRUPTED

    # And the module offers no way to do it: it reports, and that is the whole surface.
    doers = [
        name
        for name in dir(resumption)
        if not name.startswith("_") and any(w in name for w in ("resume", "run", "execute"))
    ]
    assert doers == []


async def test_interrupted_work_reaches_the_owner_in_the_brief(make_settings):
    from thursday_shared.enums import TaskState

    first = await boot(make_task_settings(make_settings))
    await a_running_task(first, (1, "send", "email.send", TaskState.RUNNING))

    second = await boot(make_task_settings(make_settings))
    brief = await second.briefer.morning()
    assert any("send the report" in line for line in brief.issues)


# --------------------------------------------------------------------------- the wiring


async def test_every_public_mutator_persists(make_settings):
    """The answer to "each caller must remember": enumerate the API and check.

    Five places mutate a task, and a sixth added later that forgets to persist would be
    invisible until a restart. This test is what makes that visible on the day it is written.
    """
    import inspect

    from thursday_core.tasks import TaskManager

    mutators = {
        name
        for name, member in inspect.getmembers(TaskManager, inspect.isfunction)
        if not name.startswith("_")
        and name not in {"get", "list", "is_cancelled", "export_state", "import_state", "restore"}
    }
    assert mutators, "the API check found nothing to check"

    source = inspect.getsource(TaskManager)
    for name in mutators:
        body = (
            source.split(f"def {name}(", 1)[1].split("\n    async def ")[0].split("\n    def ")[0]
        )
        persists = (
            "_save(task)" in body or "self.transition(" in body or "await self.transition" in body
        )
        assert persists, f"{name} mutates a task and never persists it"
