"""Measurements that survive a restart, and die with the hardware (ADDENDUM §25) — Sprint 103.

§23 listed this gap and named the thing blocking it:

> **Benchmarks do not survive a restart.** The `models` table has `tokens_per_second` and
> `last_benchmarked_at` waiting for them. Persisting needs a decision about whether a
> measurement taken before a hardware change should outlive it, and guessing that is worse
> than restarting the window.

Measured against the shipped code before any of this existed:

    AFTER 5 REAL CALLS: 500.0 tok/s | profiles held: 1
    AFTER A RESTART:      0.0 tok/s | profiles held: 1
    FRESHNESS WINDOW: 14 days
    REPOSITORY WIRED: None

`BenchmarkBook.__init__` took a `repository`, assigned it to `self._repository`, and never
read it again — a persistence hook that persisted nothing. So the fourteen-day window sat on
top of data that could not survive an afternoon, and **had never once applied**.

The decision §23 was waiting for does not need a guess. A measurement describes a model
running on particular hardware, and the machine says what hardware that is. Keep it while the
machine still answers to the same description; discard it when it does not.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from thursday_core.benchmarks import (
    CLOUD_FINGERPRINT,
    MAX_AGE,
    BenchmarkBook,
    fingerprint_of,
    key_for,
    row_id_for,
)
from thursday_shared.compute import GIB, ComputeProfile
from thursday_shared.ids import new_id

GPU_BOX = new_id()
MODEL = "llama3:8b"

WORKSTATION = ComputeProfile(
    gpu_name="RTX 4090", vram_bytes=24 * GIB, ram_bytes=64 * GIB, cpu_cores=16
)
#: The same machine after somebody put a different card in it.
UPGRADED = ComputeProfile(
    gpu_name="RTX 5090", vram_bytes=32 * GIB, ram_bytes=64 * GIB, cpu_cores=16
)
#: The same hardware, renamed. Not a reason to throw a measurement away.
RENAMED = ComputeProfile(gpu_name="RTX 4090", vram_bytes=24 * GIB, ram_bytes=64 * GIB, cpu_cores=16)


class Hub:
    """A device hub reporting one machine's hardware."""

    def __init__(self, compute: ComputeProfile | None) -> None:
        self._compute = compute

    def summary(self, device_id: UUID) -> object:
        return type("Summary", (), {"compute": self._compute})()


class Repo:
    """The table, as far as the book is concerned."""

    def __init__(self) -> None:
        self.rows: dict[object, dict] = {}

    async def load(self) -> list[dict]:
        return list(self.rows.values())

    async def put(self, row: dict) -> None:
        self.rows[row["id"]] = row

    async def remove(self, key: object) -> None:
        self.rows.pop(key, None)

    async def clear(self) -> None:
        self.rows.clear()


def book(repo: Repo, hardware: ComputeProfile | None = WORKSTATION) -> BenchmarkBook:
    return BenchmarkBook(repository=repo, hub=Hub(hardware))


def measure(b: BenchmarkBook, *, calls: int = 5, at: datetime | None = None) -> None:
    """Enough real calls to clear MIN_SAMPLES, so the book reports a number at all."""
    for _ in range(calls):
        b.record(device_id=GPU_BOX, model=MODEL, latency_ms=800, tokens_out=400, at=at)


# ----------------------------------------------------------------- surviving a restart


async def test_a_measurement_survives_a_restart():
    """The whole point. Five real calls, a new process, and the router still knows."""
    repo = Repo()
    first = book(repo)
    measure(first)
    assert first.speed_of(GPU_BOX, MODEL) == 500.0
    assert await first.flush() == 1

    second = book(repo)
    assert await second.restore() == 1
    assert second.speed_of(GPU_BOX, MODEL) == 500.0


async def test_nothing_is_written_until_it_is_flushed():
    """`record` is synchronous and on the path of every model call. An await there costs
    more than the handful of samples a crash between flushes loses out of fifty."""
    repo = Repo()
    b = book(repo)
    measure(b)
    assert repo.rows == {}

    await b.flush()
    assert len(repo.rows) == 1


async def test_a_profile_rewrites_its_own_row_rather_than_adding_one():
    """The row id is derived from the key, so flushing twice leaves one row and not two."""
    repo = Repo()
    b = book(repo)
    measure(b)
    await b.flush()
    measure(b)
    await b.flush()

    assert len(repo.rows) == 1
    assert row_id_for(key_for(GPU_BOX, MODEL)) in repo.rows


async def test_a_flush_with_nothing_new_writes_nothing():
    repo = Repo()
    b = book(repo)
    measure(b)
    assert await b.flush() == 1
    assert await b.flush() == 0


# ----------------------------------------------------------------- dying with the hardware


async def test_a_measurement_does_not_outlive_the_hardware_it_described():
    """§23's blocking decision, answered by reading the machine rather than by guessing: a
    number measured on a 4090 says nothing about the 5090 that replaced it."""
    repo = Repo()
    before = book(repo, WORKSTATION)
    measure(before)
    await before.flush()

    after = book(repo, UPGRADED)
    assert await after.restore() == 0
    assert after.speed_of(GPU_BOX, MODEL) == 0.0, "0.0 means unmeasured, which is now true"


async def test_a_discarded_measurement_is_removed_rather_than_left_to_be_re_read():
    """Left in place it would be re-examined and re-rejected on every start, for ever."""
    repo = Repo()
    before = book(repo, WORKSTATION)
    measure(before)
    await before.flush()
    assert repo.rows

    await book(repo, UPGRADED).restore()
    assert repo.rows == {}


async def test_renaming_a_machine_does_not_throw_its_measurements_away():
    """The fingerprint is the hardware, not the label. A machine called something else is
    still the machine that produced those numbers."""
    repo = Repo()
    measure(book_ := book(repo, WORKSTATION))
    await book_.flush()

    assert await book(repo, RENAMED).restore() == 1


async def test_a_machine_that_has_not_reported_is_not_the_same_as_the_cloud():
    """Sharing a string would let a measurement taken before the report survive a swap."""
    assert fingerprint_of(None) != CLOUD_FINGERPRINT


async def test_the_cloud_is_never_discarded_for_hardware():
    """A provider's hardware is theirs and changes without telling anybody, so there is
    nothing to compare. The freshness window is the only bound, and this says so rather than
    implying a check that is not happening."""
    repo = Repo()
    b = book(repo)
    for _ in range(5):
        b.record(device_id=None, model="cloud-reasoning", latency_ms=900, tokens_out=450)
    await b.flush()

    restored = book(repo, UPGRADED)
    assert await restored.restore() == 1
    assert restored.speed_of(None, "cloud-reasoning") == 500.0


# ----------------------------------------------------------------- the window that never was


async def test_samples_past_the_window_are_dropped_on_the_way_back_in():
    """The fourteen days were always in the code and had never once applied, because nothing
    lived long enough to reach them."""
    repo = Repo()
    old = book(repo)
    measure(old, at=datetime.now(UTC) - MAX_AGE - timedelta(days=1))
    await old.flush()

    assert await book(repo).restore() == 0


async def test_a_profile_with_nothing_left_inside_the_window_is_removed():
    repo = Repo()
    old = book(repo)
    measure(old, at=datetime.now(UTC) - MAX_AGE - timedelta(days=1))
    await old.flush()

    await book(repo).restore()
    assert repo.rows == {}


async def test_a_mix_of_old_and_fresh_samples_keeps_only_the_fresh_ones():
    repo = Repo()
    b = book(repo)
    measure(b, calls=5, at=datetime.now(UTC) - MAX_AGE - timedelta(days=1))
    measure(b, calls=5)
    await b.flush()

    restored = book(repo)
    assert await restored.restore() == 1
    assert len(restored.profile(GPU_BOX, MODEL).samples) == 5


# ----------------------------------------------------------------- refusing to be brittle


async def test_one_unreadable_sample_does_not_fail_the_restore():
    """Refusing to start because of a bad row turns a rounding error into an outage."""
    repo = Repo()
    b = book(repo)
    measure(b)
    await b.flush()

    row = next(iter(repo.rows.values()))
    row["samples"].append({"at": "not-a-date"})
    row["samples"].append("not even a dict")

    restored = book(repo)
    assert await restored.restore() == 1
    assert len(restored.profile(GPU_BOX, MODEL).samples) == 5


async def test_a_book_with_no_repository_still_measures():
    """No database is a supported configuration, not a degraded one — the book falls back to
    the in-process window rather than to a quiet promise of durability.

    It still flushes, into the null store: short-circuiting would leave `_dirty` growing for
    the life of the process, and the write-through genuinely happened into the store this
    deployment configured. What it does not do is come back."""
    b = BenchmarkBook()
    measure(b)
    assert b.speed_of(GPU_BOX, MODEL) == 500.0
    assert await b.flush() == 1
    assert await b.flush() == 0, "the dirty set must clear, database or not"
    assert await b.restore() == 0


async def test_a_book_with_no_hub_stamps_a_fingerprint_it_can_still_compare():
    """Without a hub every local measurement reads as unreported, which restores only onto
    another machine that has also not reported — never onto a known, different one."""
    repo = Repo()
    b = BenchmarkBook(repository=repo)
    measure(b)
    await b.flush()

    assert await BenchmarkBook(repository=repo).restore() == 1
    assert await book(repo, WORKSTATION).restore() == 0


@pytest.mark.parametrize("model_name", ["llama3:8b", "weird|name|with|pipes"])
async def test_a_key_round_trips_even_when_the_model_name_contains_the_separator(model_name):
    """`key_for` joins on `|`, and a model name may contain one. Splitting from the right
    would put half the name into the device id."""
    repo = Repo()
    b = book(repo)
    for _ in range(5):
        b.record(device_id=GPU_BOX, model=model_name, latency_ms=800, tokens_out=400)
    await b.flush()

    restored = book(repo)
    assert await restored.restore() == 1
    assert restored.speed_of(GPU_BOX, model_name) == 500.0
