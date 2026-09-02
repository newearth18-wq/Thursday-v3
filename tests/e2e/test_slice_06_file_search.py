"""The second command: "Thursday หาไฟล์ Excel ล่าสุดใน Downloads".

Read-only by construction — `file.search` is a READ-level tool and nothing in this path can
modify a file. What the tests hold to is that the answer is the *right* file and that the
reply says which one, because "I searched your Downloads folder" is a report about
Thursday's activity, not about the owner's files.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from thursday_core import intent_rules
from thursday_devices.fake import FakeDeviceNode
from thursday_shared.enums import PermissionLevel, TaskState
from thursday_shared.ids import new_id
from thursday_shared.models import DeviceAction, UserRequest


def aged(path: Path, hours: float, body: str = "x") -> Path:
    path.write_text(body, encoding="utf-8")
    stamp = time.time() - hours * 3600
    os.utime(path, (stamp, stamp))
    return path


@pytest.fixture
def downloads(tmp_path: Path) -> Path:
    folder = tmp_path / "Downloads"
    folder.mkdir()
    return folder


@pytest.fixture
async def pc(container, tmp_path):
    node = FakeDeviceNode(name="Office-PC", allowed_roots=[tmp_path])
    session = node.session()
    await container.hub.register(session)
    container.world.update(active_device_id=session.device_id, active_device_name="Office-PC")
    return session


# ------------------------------------------------------------------ understanding


def test_the_sentence_is_understood_without_a_model_call():
    match = intent_rules.parse("Thursday หาไฟล์ Excel ล่าสุดใน Downloads")
    assert match is not None and match.confident
    entities = match.intent.entities
    assert entities["action"] == "file.search"
    assert entities["root"] == "~/Downloads"
    # Excel means both extensions. Answering with only .xlsx would quietly miss the file.
    assert set(entities["pattern"]) >= {"*.xlsx", "*.xls"}
    assert entities["limit"] == 1  # "ล่าสุด" — the latest one, not the latest twenty


def test_the_english_phrasing_lands_in_the_same_place():
    a = intent_rules.parse("Thursday หาไฟล์ Excel ล่าสุดใน Downloads")
    b = intent_rules.parse("Thursday find the latest excel file in Downloads")
    assert a is not None and b is not None
    assert a.intent.entities == b.intent.entities


def test_an_unrecognised_folder_is_not_guessed_at():
    """Falling back to the home directory with `*` would walk the whole disk to answer a
    question nobody asked. Unrecognised goes to the model instead."""
    assert intent_rules.parse("Thursday find excel in the usual place") is None


# ------------------------------------------------------------------ the search itself


async def test_the_newest_file_is_found_across_both_extensions(tmp_path, downloads):
    """The ordering bug this guards against: truncating during the walk and sorting the
    survivors returns the newest of an arbitrary subset, and looks entirely plausible."""
    for index in range(60):
        aged(downloads / f"sheet{index:02d}.xlsx", hours=100 + index)
    newest = aged(downloads / "Sales-Aug.xls", hours=1)

    node = FakeDeviceNode(allowed_roots=[tmp_path])
    result = await node.executor.execute(
        DeviceAction(
            action="file.search",
            args={"root": str(downloads), "pattern": ["*.xlsx", "*.xls"], "limit": 1},
        )
    )

    assert result.ok and result.verified
    assert result.data["matches"][0]["path"] == str(newest)
    assert result.evidence["scanned"] == 61


async def test_the_search_cannot_leave_the_node_s_allowed_roots(tmp_path):
    node = FakeDeviceNode(allowed_roots=[tmp_path / "sandbox"])
    (tmp_path / "sandbox").mkdir()
    result = await node.executor.execute(
        DeviceAction(action="file.search", args={"root": "/etc", "pattern": "*.conf"})
    )
    assert not result.ok
    assert "allowed roots" in result.error


async def test_a_partial_walk_says_so(tmp_path, downloads):
    """ "The newest of what I looked at" is a different claim from "the newest"."""
    for index in range(12):
        aged(downloads / f"f{index}.xlsx", hours=index)

    node = FakeDeviceNode(allowed_roots=[tmp_path])
    result = await node.executor.execute(
        DeviceAction(
            action="file.search",
            args={"root": str(downloads), "pattern": "*.xlsx", "limit": 5, "scan_cap": 6},
        )
    )
    assert result.data["truncated"] is True


async def test_an_unreadable_entry_does_not_fail_the_whole_search(tmp_path, downloads):
    aged(downloads / "good.xlsx", hours=1)
    (downloads / "subdir.xlsx").mkdir()  # a directory matching the glob is not a file

    node = FakeDeviceNode(allowed_roots=[tmp_path])
    result = await node.executor.execute(
        DeviceAction(action="file.search", args={"root": str(downloads), "pattern": "*.xlsx"})
    )
    assert result.ok
    assert [m["name"] for m in result.data["matches"]] == ["good.xlsx"]


# ------------------------------------------------------------------ end to end


async def test_the_reply_names_the_file(container, pc, downloads, session_id):
    aged(downloads / "budget_q1.xlsx", hours=200)
    aged(downloads / "old_report.xls", hours=900)
    aged(downloads / "Sales-Aug.xlsx", hours=3)

    response = await container.engine.handle_request(
        UserRequest(
            conversation_id=session_id,
            text=f"Thursday หาไฟล์ Excel ล่าสุดใน {downloads}",
            device_id=pc.device_id,
        )
    )

    assert "Sales-Aug.xlsx" in response.text
    assert response.verified is True
    assert response.status is TaskState.COMPLETED
    # It answered about the files, not about itself.
    assert "file.search" not in response.text


async def test_an_empty_folder_is_reported_honestly(container, pc, downloads, session_id):
    response = await container.engine.handle_request(
        UserRequest(
            conversation_id=session_id,
            text=f"Thursday find pdf files in {downloads}",
            device_id=pc.device_id,
        )
    )
    assert "no matching files" in response.text


async def test_searching_is_a_read_and_needs_no_approval(container, pc, downloads, session_id):
    aged(downloads / "a.xlsx", hours=1)
    spec = container.tools.specs()
    search = next(s for s in spec if s.name == "file.search")
    assert search.permission is PermissionLevel.READ

    await container.engine.handle_request(
        UserRequest(
            conversation_id=new_id(),
            text=f"Thursday หาไฟล์ Excel ล่าสุดใน {downloads}",
            device_id=pc.device_id,
        )
    )
    assert not container.approvals.pending()
    # And nothing was written: the file is exactly as it was.
    assert (downloads / "a.xlsx").read_text(encoding="utf-8") == "x"
