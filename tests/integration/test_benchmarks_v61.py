"""Model benchmarks and success history (ADDENDUM §25, §26) — Sprint 61.

§25 asks for tokens/sec, time-to-first-token, latency and success rate, and says what they are
for in four words: *use real data to adjust routing*. §26 adds the history — model A succeeds
96% of the time, model B 82%, prefer A when quality is what was asked for.

Measurement that feeds routing has a failure mode that measurement for a dashboard does not:
**a bad number changes which model runs next, and a model that stops running is never measured
again.** Three of the tests below are about closing that loop, and each of them describes a way
a reasonable implementation makes routing worse than no measurement at all.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from thursday_core.benchmarks import (
    MAX_AGE,
    MIN_SAMPLES,
    BenchmarkBook,
    Fault,
    key_for,
)
from thursday_core.compute_router import ComputeRequest, ComputeRouter, RoutingProfile
from thursday_core.model_registry import ModelRegistry
from thursday_shared.compute import GIB, ComputeProfile, ModelDescriptor, ModelState, RuntimeKind
from thursday_shared.ids import new_id

GPU_PC, LAPTOP = new_id(), new_id()
WORKSTATION = ComputeProfile(gpu_name="RTX 4090", vram_bytes=24 * GIB, ram_bytes=64 * GIB)
HEADLESS = ComputeProfile(vram_bytes=0, ram_bytes=64 * GIB)


def book_with(**kw) -> BenchmarkBook:
    return BenchmarkBook(**kw)


def good_run(book: BenchmarkBook, model: str, *, device_id=GPU_PC, tokens=500, ms=10_000, **kw):
    return book.record(
        device_id=device_id, model=model, latency_ms=ms, tokens_out=tokens, ok=True, **kw
    )


# --------------------------------------------------------------------------- measurement


def test_throughput_is_measured_from_real_calls():
    book = book_with()
    for _ in range(MIN_SAMPLES):
        good_run(book, "llama3", tokens=500, ms=10_000)

    assert book.speed_of(GPU_PC, "llama3") == 50.0


def test_a_model_with_too_few_samples_reads_as_unmeasured_not_as_slow():
    """Zero means unmeasured, and the router reads it that way (ADR 0046). Returning a
    provisional number from two samples would be worse than returning none: the router
    cannot tell a guess from a measurement, so it must not be handed one."""
    book = book_with()
    for _ in range(MIN_SAMPLES - 1):
        good_run(book, "llama3", tokens=10, ms=10_000)

    assert book.speed_of(GPU_PC, "llama3") == 0.0
    assert book.profile(GPU_PC, "llama3").measured is False


def test_one_terrible_sample_does_not_damn_a_model():
    """The loop this sprint is really about.

    A single call during a backup, a thermal event or a suspend/resume is enough to move a
    *mean* by an order of magnitude. Routing then moves away, the model stops running, and
    it is never re-measured — the bad number becomes permanent. A median needs half the
    samples to be bad before it moves at all.
    """
    book = book_with()
    for _ in range(9):
        good_run(book, "llama3", tokens=500, ms=10_000)  # 50 tok/s
    good_run(book, "llama3", tokens=5, ms=60_000)  # 0.08 tok/s, during something awful

    assert book.speed_of(GPU_PC, "llama3") == 50.0


def test_a_cold_model_is_not_recorded_as_a_slow_one():
    """§22. The first call after a model is paged in from disk measures the disk. A
    40-second first token would otherwise make a good model look unusable for as long as the
    window remembers it."""
    book = book_with()
    for _ in range(MIN_SAMPLES):
        good_run(book, "llama3", tokens=500, ms=10_000)
    for _ in range(MIN_SAMPLES):
        good_run(book, "llama3", tokens=500, ms=120_000, state=ModelState.UNLOADED)

    assert book.speed_of(GPU_PC, "llama3") == 50.0, "cold calls leaked into the speed figure"


def test_stale_samples_are_dropped():
    """Hardware changes, models are re-quantised, drivers are updated. A number from six
    weeks ago describes a machine that may no longer exist."""
    book = book_with()
    long_ago = datetime.now(UTC) - MAX_AGE - timedelta(days=1)
    for _ in range(MIN_SAMPLES):
        good_run(book, "llama3", tokens=5000, ms=1_000, at=long_ago)

    assert book.speed_of(GPU_PC, "llama3") == 0.0


def test_the_same_model_on_two_machines_is_measured_separately():
    """A 7B model is not the same thing on a 4090 and on a laptop, and averaging them would
    describe neither."""
    book = book_with()
    for _ in range(MIN_SAMPLES):
        good_run(book, "llama3", device_id=GPU_PC, tokens=1000, ms=10_000)
        good_run(book, "llama3", device_id=LAPTOP, tokens=100, ms=10_000)

    assert book.speed_of(GPU_PC, "llama3") == 100.0
    assert book.speed_of(LAPTOP, "llama3") == 10.0
    assert key_for(GPU_PC, "llama3") != key_for(LAPTOP, "llama3")


def test_time_to_first_token_is_reported_separately_from_throughput():
    """§25 lists both because they answer different questions: one is how long before
    anything appears, the other is how fast it arrives once it does."""
    book = book_with()
    for _ in range(MIN_SAMPLES):
        good_run(book, "llama3", tokens=500, ms=10_000, ttft_ms=300)

    assert book.profile(GPU_PC, "llama3").time_to_first_token_ms == 300.0


# --------------------------------------------------------------------------- §26 success


def test_a_models_success_rate_comes_from_calls_it_could_have_got_right():
    book = book_with()
    for _ in range(8):
        good_run(book, "llama3")
    for _ in range(2):
        book.record(device_id=GPU_PC, model="llama3", latency_ms=500, ok=False, fault=Fault.MODEL)

    assert book.success_of(GPU_PC, "llama3") == 0.8


def test_the_network_being_down_is_not_evidence_about_the_model():
    """The failure this classification exists for.

    An unplugged machine, a dropped socket, the owner disabling a model mid-flight — none of
    these say anything about the model. Counting them would let one bad afternoon on the
    network permanently demote the best model in the house, and the demotion is invisible
    because the number looks like a measurement.
    """
    book = book_with()
    for _ in range(MIN_SAMPLES):
        good_run(book, "llama3")
    for _ in range(20):
        book.record(
            device_id=GPU_PC,
            model="llama3",
            latency_ms=30_000,
            ok=False,
            fault=Fault.INFRASTRUCTURE,
        )

    assert book.success_of(GPU_PC, "llama3") == 1.0
    # Not hidden, though: an operator needs to see that the machine is flaky.
    assert book.profile(GPU_PC, "llama3").infrastructure_failures == 20


def test_an_unclassified_failure_counts_against_the_model():
    """`UNKNOWN` is not a free pass. A failure nobody classified is still a call that did not
    produce an answer, and treating it as neutral would let an unclassified path quietly
    launder every failure it produced."""
    book = book_with()
    for _ in range(5):
        good_run(book, "llama3")
    for _ in range(5):
        book.record(device_id=GPU_PC, model="llama3", latency_ms=500, ok=False)

    assert book.success_of(GPU_PC, "llama3") == 0.5


def test_a_model_with_no_history_is_not_a_model_with_a_bad_one():
    book = book_with()
    assert book.success_of(GPU_PC, "never-run") == 0.0
    assert book.profile(GPU_PC, "never-run").measured is False


# --------------------------------------------------------------------------- routing


async def test_measured_throughput_reaches_the_router():
    """§25's four words. Measurement that does not change a decision is a dashboard."""
    registry = ModelRegistry()
    await registry.observe(GPU_PC, [ModelDescriptor(name="llama3", runtime=RuntimeKind.OLLAMA)])

    book = book_with()
    for _ in range(MIN_SAMPLES):
        good_run(book, "llama3", tokens=800, ms=10_000)

    router = ComputeRouter(registry=registry, benchmarks=book)
    [candidate] = router.candidates("ai.llm")
    assert candidate.tokens_per_second == 80.0


async def test_the_quality_profile_prefers_the_model_that_actually_succeeds():
    """§26's worked example: 96% beats 82%, and both beat the GPU a third model sits on."""
    registry = ModelRegistry()
    await registry.observe(GPU_PC, [ModelDescriptor(name="unreliable", runtime=RuntimeKind.OLLAMA)])
    await registry.observe(LAPTOP, [ModelDescriptor(name="reliable", runtime=RuntimeKind.OLLAMA)])

    book = book_with()
    for _ in range(20):
        good_run(book, "reliable", device_id=LAPTOP)
    for i in range(20):
        book.record(
            device_id=GPU_PC,
            model="unreliable",
            latency_ms=500,
            ok=i < 12,
            tokens_out=100,
            fault=Fault.MODEL,
        )

    router = ComputeRouter(registry=registry, benchmarks=book)
    router._hub = _Hub({GPU_PC: WORKSTATION, LAPTOP: HEADLESS})

    target = router.choose(ComputeRequest(profile=RoutingProfile.QUALITY))

    assert target.model == "reliable", "the GPU won over the model that actually works"


async def test_an_unmeasured_model_is_not_beaten_by_a_measured_bad_one():
    """The self-fulfilling loop again, from the other side. A model with no history must not
    be treated as one with a bad history, or the first model ever measured wins for ever."""
    registry = ModelRegistry()
    await registry.observe(GPU_PC, [ModelDescriptor(name="new-model", runtime=RuntimeKind.OLLAMA)])

    book = book_with()
    router = ComputeRouter(registry=registry, benchmarks=book)
    [candidate] = router.candidates("ai.llm")

    assert candidate.tokens_per_second == 0.0
    assert candidate.success_rate == 0.0


def test_a_router_without_benchmarks_still_works():
    """Measurement improves routing; it does not enable it. The container wires a book, and
    a router built without one must behave exactly as it did before §25 existed."""
    router = ComputeRouter()
    assert router._benchmarks is None
    assert router.candidates("ai.llm") == []


class _Hub:
    def __init__(self, profiles: dict) -> None:
        self._profiles = profiles

    def summary(self, device_id):
        profile = self._profiles.get(device_id)
        return type("S", (), {"compute": profile, "load": None})() if profile else None


# --------------------------------------------------------------------------- reporting


def test_the_report_separates_measured_models_from_unmeasured_ones():
    """An operator asking "what does Thursday know about its models" needs the difference
    between a slow model and one nobody has used."""
    book = book_with()
    for _ in range(MIN_SAMPLES):
        good_run(book, "measured")
    good_run(book, "barely-used")

    report = book.report()
    assert report["measured"] == 1
    assert report["unmeasured"] == 1
    assert {m["key"] for m in report["models"]} == {
        key_for(GPU_PC, "measured"),
        key_for(GPU_PC, "barely-used"),
    }


@pytest.mark.parametrize("tokens,ms", [(0, 1000), (100, 0), (-5, 1000)])
def test_a_sample_that_cannot_yield_a_rate_is_not_counted_as_zero(tokens, ms):
    """Zero tokens in a second is not "zero tokens per second" — it is a call that produced
    no measurement, and folding it in as zero would drag every median down."""
    book = book_with()
    for _ in range(MIN_SAMPLES):
        good_run(book, "llama3", tokens=500, ms=10_000)
        book.record(device_id=GPU_PC, model="llama3", latency_ms=ms, tokens_out=tokens, ok=True)

    assert book.speed_of(GPU_PC, "llama3") == 50.0
