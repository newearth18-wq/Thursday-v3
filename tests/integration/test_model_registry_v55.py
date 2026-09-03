"""The model registry (ADDENDUM §5, §48–§50) — Sprint 55.

Acceptance: "Thursday knows which model exists on which machine." The schema half of that is
dull. The interesting half is a conflict rule.

Sprint 54 taught nodes to report their inventory, and part of that report is a **guess**: no
runtime says what a model is for, so discovery reads the name. The guess is usually right,
sometimes wrong, and unreadable for a private build. So the owner can correct it — and the
correction has to survive the next reconnect, or it is not a correction, it is a suggestion
that expires the next time the machine reboots.

That is §110's rule about memory, applied to compute: what a source *reports* can never
redefine what the owner has *said*.
"""

from __future__ import annotations

import pytest
from thursday_core.model_registry import ModelRegistry, RegisteredModel, model_id_for
from thursday_shared.compute import GIB, ModelDescriptor, ModelKind, RuntimeKind
from thursday_shared.errors import ThursdayError
from thursday_shared.ids import new_id

GPU_PC = new_id()
LAPTOP = new_id()


def ollama(name: str, kind: ModelKind = ModelKind.LLM, **kw) -> ModelDescriptor:
    return ModelDescriptor(name=name, kind=kind, runtime=RuntimeKind.OLLAMA, **kw)


@pytest.fixture
def registry() -> ModelRegistry:
    return ModelRegistry()


# --------------------------------------------------------------------------- identity


def test_the_same_model_on_two_machines_is_two_entries(registry):
    """Because routing chooses between them. Collapsing them by name would make "where does
    this model live" unanswerable, which is the question the sprint exists to answer."""
    assert model_id_for(GPU_PC, RuntimeKind.OLLAMA, "llama3") != model_id_for(
        LAPTOP, RuntimeKind.OLLAMA, "llama3"
    )


def test_the_same_name_on_two_runtimes_is_two_entries():
    """They load, unload and fail independently, so they are two things to route to."""
    assert model_id_for(GPU_PC, RuntimeKind.OLLAMA, "llama3") != model_id_for(
        GPU_PC, RuntimeKind.LM_STUDIO, "llama3"
    )


def test_an_id_is_stable_across_reconnects():
    """Derived rather than allocated: a node that reconnects must land on the row it had,
    and lookup-then-insert races with itself when two reports arrive together."""
    first = model_id_for(GPU_PC, RuntimeKind.OLLAMA, "llama3")
    assert first == model_id_for(GPU_PC, RuntimeKind.OLLAMA, "llama3")


# --------------------------------------------------------------------------- the conflict rule


async def test_an_owners_correction_survives_the_node_reconnecting(registry):
    """The rule this sprint is really about.

    A correction that the next reconnect overwrites is worse than no correction at all: the
    owner watched it work and has no reason to check it again.
    """
    mistaken = ollama("house-model-v3")  # guessed LLM from an unreadable name
    [entry] = await registry.observe(GPU_PC, [mistaken])
    assert entry.kind is ModelKind.LLM

    await registry.set_kind(entry.id, ModelKind.VISION)
    assert registry.get(entry.id).kind is ModelKind.VISION

    # The node restarts and reports the same inventory, with the same wrong guess.
    await registry.observe(GPU_PC, [ollama("house-model-v3")])

    corrected = registry.get(entry.id)
    assert corrected.kind is ModelKind.VISION, "the correction was overwritten by a guess"
    assert corrected.observed.kind is ModelKind.LLM, "what the node said is still recorded"
    assert corrected.capability == "ai.vision"


async def test_a_correction_can_be_cleared_and_the_guess_trusted_again(registry):
    [entry] = await registry.observe(GPU_PC, [ollama("llava:13b", ModelKind.VISION)])
    await registry.set_kind(entry.id, ModelKind.LLM)
    assert registry.get(entry.id).kind is ModelKind.LLM

    await registry.set_kind(entry.id, None)
    assert registry.get(entry.id).kind is ModelKind.VISION


async def test_disabling_a_model_is_a_decision_not_a_preference(registry):
    """An owner who switches a model off has given an instruction. It outranks every routing
    preference, and it is not undone by the node re-reporting the model as present."""
    [entry] = await registry.observe(GPU_PC, [ollama("llama3:70b")])
    assert entry.usable is True

    await registry.set_enabled(entry.id, False)
    await registry.observe(GPU_PC, [ollama("llama3:70b")])

    assert registry.get(entry.id).enabled is False
    assert registry.get(entry.id).usable is False
    assert registry.for_capability("ai.llm") == []


def test_no_opinion_and_switched_off_are_different_facts():
    """Tri-state, so a default cannot be mistaken for a decision."""
    entry = RegisteredModel(id=new_id(), device_id=None, observed=ollama("m"), online=True)
    assert entry.enabled_override is None and entry.enabled is True

    entry.enabled_override = False
    assert entry.enabled is False


# --------------------------------------------------------------------------- presence


async def test_a_disconnected_machines_models_go_offline_rather_than_away(registry):
    """Unreachable is not uninstalled. Deleting them would take the owner's corrections with
    them, and a GPU box that is merely asleep would lose its configuration every night."""
    [entry] = await registry.observe(GPU_PC, [ollama("llava:13b", ModelKind.VISION)])
    await registry.set_kind(entry.id, ModelKind.OCR)

    assert await registry.device_offline(GPU_PC) == 1

    kept = registry.get(entry.id)
    assert kept is not None, "the model was deleted, not marked offline"
    assert kept.online is False and kept.usable is False
    assert kept.kind is ModelKind.OCR, "the correction went with it"


async def test_a_model_that_stops_being_reported_goes_offline_not_away(registry):
    """Ollama restarting mid-scan is not the owner uninstalling a model."""
    await registry.observe(GPU_PC, [ollama("a"), ollama("b")])
    await registry.observe(GPU_PC, [ollama("a")])

    entries = {m.name: m for m in registry.on_device(GPU_PC)}
    assert entries["a"].online is True
    assert entries["b"].online is False, "b should be offline"
    assert len(entries) == 2, "b should still exist"


async def test_one_machine_going_offline_does_not_touch_another(registry):
    await registry.observe(GPU_PC, [ollama("shared-name")])
    await registry.observe(LAPTOP, [ollama("shared-name")])

    await registry.device_offline(GPU_PC)

    assert [m.online for m in registry.on_device(LAPTOP)] == [True]


# --------------------------------------------------------------------------- queries


async def test_capability_queries_use_the_same_vocabulary_as_the_device_layer(registry):
    """`ai.vision`, not `ModelKind.VISION`, so callers do not translate between two
    vocabularies at every call site."""
    await registry.observe(
        GPU_PC,
        [
            ollama("llava:13b", ModelKind.VISION, required_vram_bytes=8 * GIB),
            ollama("nomic-embed-text", ModelKind.EMBEDDING),
            ollama("llama3:8b"),
        ],
    )
    assert [m.name for m in registry.for_capability("ai.vision")] == ["llava:13b"]
    assert registry.devices_with("ai.embedding") == {GPU_PC}


async def test_an_offline_model_is_not_offered_for_a_capability(registry):
    await registry.observe(GPU_PC, [ollama("llava:13b", ModelKind.VISION)])
    await registry.device_offline(GPU_PC)

    assert registry.for_capability("ai.vision") == []
    assert len(registry.for_capability("ai.vision", usable_only=False)) == 1


async def test_health_counts_what_an_operator_would_ask(registry):
    entries = await registry.observe(GPU_PC, [ollama("a"), ollama("b"), ollama("c")])
    await registry.set_kind(entries[0].id, ModelKind.VISION)
    await registry.set_enabled(entries[1].id, False)

    assert registry.health() == {"models": 3, "online": 3, "disabled": 1, "corrected": 1}


async def test_correcting_a_model_that_does_not_exist_says_so(registry):
    with pytest.raises(ThursdayError, match="unknown model"):
        await registry.set_kind(new_id(), ModelKind.VISION)


# --------------------------------------------------------------------------- persistence


async def test_a_registry_round_trips_through_its_repository(registry):
    """Corrections are the reason this is persisted at all — the inventory itself is
    re-reported on every reconnect."""

    class Rows:
        def __init__(self):
            self.rows: dict = {}

        async def put(self, row):
            self.rows[row["id"]] = row

        async def load(self):
            return list(self.rows.values())

    store = Rows()
    first = ModelRegistry(repository=store)
    [entry] = await first.observe(GPU_PC, [ollama("house-model-v3")])
    await first.set_kind(entry.id, ModelKind.VISION)
    await first.set_enabled(entry.id, False)

    second = ModelRegistry(repository=store)
    assert await second.restore() == 1
    restored = second.get(entry.id)
    assert restored.kind is ModelKind.VISION
    assert restored.enabled is False
    assert restored.observed.kind is ModelKind.LLM


async def test_nothing_comes_back_online_from_storage(registry):
    """A registry that restored `online: true` would offer the router a machine that has
    been switched off since last week. Online is asserted by a node on *this* run."""

    class Rows:
        def __init__(self, rows):
            self.rows = rows

        async def put(self, row):
            self.rows.append(row)

        async def load(self):
            return list(self.rows)

    store = Rows([])
    first = ModelRegistry(repository=store)
    await first.observe(GPU_PC, [ollama("llama3")])
    assert store.rows[-1]["online"] is True

    second = ModelRegistry(repository=Rows(store.rows))
    await second.restore()
    assert all(m.online is False for m in second.all())
    assert second.for_capability("ai.llm") == []


# --------------------------------------------------------------------------- acceptance


@pytest.fixture
def client(settings, container):
    from fastapi.testclient import TestClient
    from thursday_api.app import create_app

    container.device_auth.required = False
    app = create_app(settings, container=container)
    app.state.container = container
    with TestClient(app) as http:
        yield http


def connect(client, *, name: str, device_id, models):
    import json

    from thursday_shared.compute import capabilities_for
    from thursday_shared.models import DeviceCapabilities, DeviceTelemetry
    from thursday_shared.protocol import Hello

    hello = Hello(
        device_id=device_id,
        name=name,
        os="Linux",
        capabilities=DeviceCapabilities.of("app.open").grant(*capabilities_for(models)),
        telemetry=DeviceTelemetry(),
        models=models,
        nonce=f"hello-{name}",
    )
    ws = client.websocket_connect("/api/v1/device")
    socket = ws.__enter__()
    socket.send_text(hello.model_dump_json())
    assert json.loads(socket.receive_text())["type"] == "WELCOME"
    return ws, socket


def test_thursday_knows_which_model_exists_on_which_machine(client, container):
    """The sprint's acceptance criterion, end to end and across two machines."""
    gpu, laptop = new_id(), new_id()
    ws_a, _ = connect(
        client,
        name="GPU-PC",
        device_id=gpu,
        models=[
            ollama("llava:13b", ModelKind.VISION, required_vram_bytes=8 * GIB),
            ollama("llama3:70b"),
        ],
    )
    ws_b, _ = connect(client, name="Laptop", device_id=laptop, models=[ollama("llama3:8b")])

    listed = client.get("/api/v1/models").json()
    by_name = {m["name"]: m for m in listed["models"]}

    assert by_name["llava:13b"]["device_id"] == str(gpu)
    assert by_name["llama3:8b"]["device_id"] == str(laptop)
    assert by_name["llava:13b"]["capability"] == "ai.vision"
    assert listed["health"]["models"] == 3

    vision = client.get("/api/v1/models", params={"capability": "ai.vision"}).json()["models"]
    assert [m["name"] for m in vision] == ["llava:13b"]

    ws_a.__exit__(None, None, None)
    ws_b.__exit__(None, None, None)


def test_a_machine_that_disconnects_keeps_its_models_listed_as_offline(client, container):
    """The owner asking "what can the GPU box run" should get an answer while it is asleep."""
    gpu = new_id()
    ws, _ = connect(client, name="GPU-PC", device_id=gpu, models=[ollama("llava:13b")])
    ws.__exit__(None, None, None)

    listed = client.get("/api/v1/models").json()["models"]
    assert [m["online"] for m in listed] == [False]
    assert [m["name"] for m in listed] == ["llava:13b"]
    assert client.get("/api/v1/models", params={"include_offline": False}).json()["models"] == []


def test_the_owner_corrects_a_guess_over_http_and_it_sticks(client, container):
    """Discovery reads the name; `house-model-v3` is unreadable and gets guessed as an LLM.
    The correction has to outlive the node reconnecting, which is what makes it a correction
    rather than a suggestion that expires."""
    gpu = new_id()
    ws, _ = connect(client, name="GPU-PC", device_id=gpu, models=[ollama("house-model-v3")])
    model_id = client.get("/api/v1/models").json()["models"][0]["id"]

    corrected = client.post(f"/api/v1/models/{model_id}/kind", params={"kind": "vision"})
    assert corrected.status_code == 200
    assert corrected.json() == {"id": model_id, "kind": "vision", "guessed": "llm"}
    ws.__exit__(None, None, None)

    # The machine comes back with the same unreadable name and the same wrong guess.
    ws2, _ = connect(client, name="GPU-PC", device_id=gpu, models=[ollama("house-model-v3")])
    after = client.get("/api/v1/models").json()["models"][0]
    assert after["kind"] == "vision"
    assert after["guessed_kind"] == "llm"
    assert after["corrected"] is True
    ws2.__exit__(None, None, None)


def test_an_unknown_kind_is_refused_rather_than_stored(client, container):
    gpu = new_id()
    ws, _ = connect(client, name="GPU-PC", device_id=gpu, models=[ollama("m")])
    model_id = client.get("/api/v1/models").json()["models"][0]["id"]

    assert (
        client.post(f"/api/v1/models/{model_id}/kind", params={"kind": "telepathy"}).status_code
        == 422
    )
    assert client.get("/api/v1/models").json()["models"][0]["kind"] == "llm"
    ws.__exit__(None, None, None)
