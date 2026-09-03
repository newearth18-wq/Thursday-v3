"""Backup and restore (Sprint 47).

A backup nobody has restored is a hope — the same lesson Sprint 46 learned about security
rules, applied to the thing whose entire value is that it works on the worst day.

So every test here does a real round trip through a real file. None of them assert that a
backup was *written*; they assert that what comes back is what went in, that a damaged one is
refused rather than half-applied, and that the file itself does not become a new place for a
credential to sit.
"""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient
from thursday_api.app import create_app
from thursday_core.backup import BackupError, BackupService, Component
from thursday_shared.enums import MemoryLayer, PolicyDecision
from thursday_shared.models import MemoryWrite

SECRET = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz012345"


@pytest.fixture
async def client(settings, container, office_pc):
    app = create_app(settings, container=container)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://thursday.test"
    ) as http:
        app.state.container = container
        yield http


@pytest.fixture
def archive(tmp_path):
    return tmp_path / "backup.json"


async def populate(container) -> dict:
    """Put something in every component, so an empty restore cannot pass by accident."""
    record = await container.memory.write(
        MemoryWrite(
            layer=MemoryLayer.SEMANTIC, content="the owner's report is due Fridays", importance=0.8
        )
    )
    task = await container.tasks.create(title="write the report", objective="Friday's report")
    await container.audit.record(
        __import__("thursday_security.audit", fromlist=["AuditEntry"]).AuditEntry(
            actor="user", action="file.write", resource="~/report.md"
        )
    )
    container.costs.record(provider="cloud", tier="STANDARD", tokens_in=90, tokens_out=10, usd=0.25)
    container.policy.override("file.copy", PolicyDecision.AUTO)
    container.journal.record("used the local model", reason="the daily cap was reached")
    return {"memory": record, "task": task}


# --------------------------------------------------------------------------- round trip


async def test_a_backup_restores_what_was_actually_in_it(container, archive):
    """The only test that matters, and the one a backup feature can ship without having."""
    seeded = await populate(container)
    container.backups.create(archive)

    # Lose everything, the way a disk does.
    container.memory.import_state([])
    container.tasks.import_state([])
    container.audit.import_state([])
    container.costs.import_state([])
    container.journal.import_state([])
    assert container.memory.export_state() == []
    assert container.costs.spent() == 0

    restored = container.backups.restore(archive, confirm=True)

    assert restored["memory"] >= 1
    assert (await container.memory.get(seeded["memory"].id)).content == seeded["memory"].content
    assert container.tasks.get(seeded["task"].id).title == "write the report"
    assert container.costs.spent() == pytest.approx(0.25)
    assert len(container.journal) == 1
    assert container.audit.export_state()


async def test_the_spending_ledger_survives_a_restart(container, archive):
    """Sprint 45 named this as its known gap: the ledger was in memory, so restarting was a
    way around the cap. A backup taken before the restart closes it."""
    container.costs.daily_usd = 1.0
    for _ in range(5):
        container.costs.record(provider="cloud", tier="FAST", usd=0.25)
    assert not container.costs.check()

    container.backups.create(archive)
    container.costs.import_state([])  # the restart
    assert container.costs.check(), "a fresh process starts with an empty ledger"

    container.backups.restore(archive, confirm=True)
    assert not container.costs.check(), "and the cap binds again once the ledger is back"


async def test_the_audit_chain_still_verifies_after_a_restore(container, archive):
    """Entries are restored with their stored hashes rather than re-recorded. A chain
    recomputed on restore is a chain that verifies whatever it was handed."""
    from thursday_security.audit import AuditEntry

    for i in range(5):
        await container.audit.record(
            AuditEntry(actor="user", action="file.write", resource=f"f{i}")
        )
    container.backups.create(archive)
    container.audit.import_state([])

    container.backups.restore(archive, confirm=True)
    assert container.audit.verify_chain() is True


async def test_an_edited_audit_entry_is_still_caught_after_a_restore(container, archive):
    """The point of keeping the hashes: somebody who edits the backup does not get a clean
    chain out of it."""
    from thursday_security.audit import AuditEntry

    for i in range(3):
        await container.audit.record(
            AuditEntry(actor="user", action="file.write", resource=f"f{i}")
        )
    container.backups.create(archive)

    document = json.loads(archive.read_text())
    document["data"]["audit"][1]["resource"] = "~/somewhere-else"
    # Rehash the component so the archive's own checksum still passes: the question is
    # whether the *audit chain* catches it once the checksum no longer can.
    from thursday_core.backup import _checksum

    document["manifest"]["checksums"]["audit"] = _checksum(document["data"]["audit"])
    archive.write_text(json.dumps(document))

    container.backups.restore(archive, confirm=True)
    assert container.audit.verify_chain() is False


# --------------------------------------------------------------------------- integrity


async def test_a_damaged_backup_is_refused_rather_than_half_applied(container, archive):
    """Half a restore leaves a system that is neither the backup nor what it was, and nobody
    can tell which parts are which."""
    await populate(container)
    container.backups.create(archive)

    document = json.loads(archive.read_text())
    document["data"]["memory"].append({"id": "junk"})
    archive.write_text(json.dumps(document))

    before = container.tasks.export_state()
    with pytest.raises(BackupError, match="did not verify"):
        container.backups.restore(archive, confirm=True)
    assert container.tasks.export_state() == before, "nothing was touched"


async def test_verify_names_what_is_wrong(container, archive):
    await populate(container)
    container.backups.create(archive)
    assert container.backups.verify(archive) == []

    document = json.loads(archive.read_text())
    document["data"]["costs"] = []
    archive.write_text(json.dumps(document))
    problems = container.backups.verify(archive)
    assert any("costs" in p for p in problems)


async def test_a_backup_from_a_newer_version_is_refused_not_half_read(container, archive):
    await populate(container)
    container.backups.create(archive)
    document = json.loads(archive.read_text())
    document["manifest"]["version"] = 999
    archive.write_text(json.dumps(document))

    with pytest.raises(BackupError, match="newer version"):
        container.backups.read(archive)


async def test_an_interrupted_write_leaves_the_previous_backup_intact(container, archive, tmp_path):
    """Written to a temporary file and moved into place. A truncated backup is worse than
    none: it looks like a backup."""
    from unittest import mock

    await populate(container)
    container.backups.create(archive)
    good = archive.read_text()

    with (
        mock.patch("pathlib.Path.write_text", side_effect=OSError("disk full")),
        pytest.raises(OSError, match="disk full"),
    ):
        container.backups.create(archive)
    assert archive.read_text() == good


def test_a_missing_backup_says_so_rather_than_restoring_nothing(container, tmp_path):
    with pytest.raises(BackupError, match="no backup"):
        container.backups.restore(tmp_path / "never-written.json", confirm=True)


# --------------------------------------------------------------------------- §90


async def test_a_backup_carries_no_credential(container, archive):
    """A backup is one more place data lands. §90's list is the principle, not the whole
    world: a credential in plain storage is a credential somebody else can read."""
    await container.memory.write(
        MemoryWrite(layer=MemoryLayer.SEMANTIC, content=f"deploy with {SECRET}", importance=0.9)
    )
    await container.audit.record(
        __import__("thursday_security.audit", fromlist=["AuditEntry"]).AuditEntry(
            actor="user", action="http.get", input_summary={"header": f"Bearer {SECRET}"}
        )
    )
    container.backups.create(archive)
    assert SECRET not in archive.read_text()


def test_the_vault_is_not_in_the_backup_at_all(container):
    """Excluded rather than redacted. Redacted secrets would restore as the redaction marker
    and quietly break every integration they belong to — and a backup that *could* restore
    the owner's keys is one that hands them over when it is stolen."""
    assert "vault" not in container.backups.components
    assert "secrets" not in container.backups.components


async def test_the_archive_is_not_world_readable(container, archive):
    import stat

    await populate(container)
    container.backups.create(archive)
    assert stat.S_IMODE(archive.stat().st_mode) == 0o600


# --------------------------------------------------------------------------- restoring is loud


def test_restoring_without_confirming_is_refused(container, archive, tmp_path):
    container.backups.create(archive)
    with pytest.raises(BackupError, match="confirm"):
        container.backups.restore(archive)


async def test_an_edited_backup_cannot_auto_approve_what_the_table_always_asks_about(
    container, archive
):
    """A backup is a file, and a file is external content. Restoring overrides goes back
    through `override`, so an edited archive cannot do what a user could not."""
    container.backups.create(archive)
    document = json.loads(archive.read_text())
    document["data"]["policies"] = [
        {"action": "file.delete", "decision": "AUTO"},
        {"action": "audit.delete", "decision": "AUTO"},
    ]
    from thursday_core.backup import _checksum

    document["manifest"]["checksums"]["policies"] = _checksum(document["data"]["policies"])
    archive.write_text(json.dumps(document))

    container.backups.restore(archive, confirm=True)
    assert container.policy.get("file.delete").default is PolicyDecision.ASK_ALWAYS
    assert container.policy.get("audit.delete").default is PolicyDecision.BLOCK


# --------------------------------------------------------------------------- the API


async def test_the_endpoints_take_verify_and_list_a_backup(client, container):
    await populate(container)
    created = await client.post("/api/v1/backups", params={"note": "before the upgrade"})
    assert created.status_code == 200
    name = created.json()["name"]

    listed = (await client.get("/api/v1/backups")).json()
    assert name in [b["name"] for b in listed["backups"]]
    assert listed["backups"][0]["problems"] == []
    assert listed["backups"][0]["note"] == "before the upgrade"

    verified = (await client.get(f"/api/v1/backups/{name}/verify")).json()
    assert verified["ok"] is True


async def test_restoring_through_the_api_needs_the_owner(client, container):
    """§95: administration is not a back door around the Permission Engine — it is exactly
    the kind of caller that would be given one."""
    await populate(container)
    name = (await client.post("/api/v1/backups")).json()["name"]

    response = await client.post(f"/api/v1/backups/{name}/restore")
    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["decision"] in {"ASK_ALWAYS", "ASK_ONCE", "BLOCK"}
    # The owner is told what they are about to lose, not asked to approve "restore".
    assert detail["restoring"]["rows"] > 0


async def test_a_backup_name_cannot_climb_out_of_the_backup_directory(client):
    """The name comes off a URL."""
    for name in ("..%2F..%2Fetc%2Fpasswd", "....//etc/passwd"):
        response = await client.post(f"/api/v1/backups/{name}/restore")
        assert response.status_code in (400, 403, 404)


# --------------------------------------------------------------------------- the wiring


def test_every_backed_up_component_can_be_read_and_written(container):
    """A component that exports and cannot import is a component that looks backed up."""
    for name in container.backups.components:
        service = {
            "memory": container.memory,
            "tasks": container.tasks,
            "audit": container.audit,
            "costs": container.costs,
            "policies": container.policy,
            "journal": container.journal,
        }[name]
        assert callable(service.export_state)
        assert callable(service.import_state)


def test_a_component_whose_export_fails_does_not_produce_a_silent_backup(tmp_path):
    """It raises. A backup that skipped the component it could not read, and said it
    succeeded, is the worst artifact this module could produce."""

    def broken() -> list[dict]:
        raise RuntimeError("the store is unreachable")

    service = BackupService([Component("broken", broken, lambda rows: 0)])
    with pytest.raises(RuntimeError, match="unreachable"):
        service.create(tmp_path / "b.json")
    assert not (tmp_path / "b.json").exists()
