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
        ),
        configure_logs=False,
    )
    ephemeral = {c["component"]: c for c in await container.health()}
    assert "lives for this process" in ephemeral["database"]["detail"]
