"""Local AI discovery (ADDENDUM §4, §29, §39, §41, §42) — Sprint 54.

The acceptance criterion is "Thursday lists available local models without manual
configuration **where safe**", and most of this file is about the last two words.

The obvious way to find local AI is to sweep the network for open inference ports. It would
work, and §29 forbids relying on it: local AI servers must not be publicly reachable, so a
process that hunts for unauthenticated ones is building the map an attacker wants — on the
owner's network — and would cheerfully route a private document to a neighbour's
misconfigured server. Discovery therefore probes loopback and configured endpoints only, and
the test that matters here is the one asserting nothing else is ever contacted.
"""

from __future__ import annotations

import inspect

import pytest
from thursday_models import local_manager, runtimes
from thursday_models.local_manager import LocalModelManager
from thursday_models.runtimes import (
    DEFAULT_ENDPOINT,
    HttpRuntime,
    NoRuntime,
    UnsafeEndpoint,
    discover,
    is_loopback,
    kind_of,
)
from thursday_shared.compute import (
    GIB,
    ComputeProfile,
    ModelDescriptor,
    ModelKind,
    RuntimeKind,
    capabilities_for,
)
from thursday_shared.ids import new_id


@pytest.fixture
def client(settings, container):
    """A live app with HELLO signature checking switched off.

    Stated plainly because the first version of this docstring said the core accepts the node
    "with no token configured", which is not what happens — Sprint 53's test proves an
    unpaired device with no token is refused, and correctly so. What this fixture actually
    does is set `required = False`, the escape hatch that exists for environments that check
    signatures elsewhere.

    That is the right trade for *this* file: these tests are about an inventory travelling
    from a node to the core, authentication has its own suite, and a fixture that re-does
    pairing here would test Sprint 36 again by accident.
    """
    from fastapi.testclient import TestClient
    from thursday_api.app import create_app

    container.device_auth.required = False
    app = create_app(settings, container=container)
    app.state.container = container
    with TestClient(app) as http:
        yield http


class FakeResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class RecordingClient:
    """Answers configured URLs, records every one it is asked for.

    The recording is the point: a test that only checks what came back cannot tell whether
    discovery also knocked on two hundred doors on the way.
    """

    def __init__(self, routes: dict[str, dict] | None = None, *, fail: set[str] | None = None):
        self.routes = routes or {}
        self.fail = fail or set()
        self.asked: list[str] = []

    async def get(self, url: str) -> FakeResponse:
        self.asked.append(url)
        for prefix in self.fail:
            if url.startswith(prefix):
                raise ConnectionError(f"nothing listening on {prefix}")
        for prefix, payload in self.routes.items():
            if url.startswith(prefix):
                return FakeResponse(200, payload)
        return FakeResponse(404, {})

    @property
    def hosts(self) -> set[str]:
        from urllib.parse import urlsplit

        return {urlsplit(u).hostname or "" for u in self.asked}


OLLAMA_TAGS = {
    "models": [
        {"name": "llama3:8b", "size": 4 * GIB, "details": {"families": ["llama"]}},
        {"name": "llava:13b", "size": 8 * GIB, "details": {"families": ["llama", "clip"]}},
        {"name": "nomic-embed-text", "size": 274 * 1024 * 1024, "details": {}},
    ]
}
LM_STUDIO_MODELS = {"data": [{"id": "qwen2.5-coder-7b"}, {"id": "whisper-large-v3"}]}


# --------------------------------------------------------------------------- what is contacted


async def test_discovery_contacts_loopback_and_nothing_else():
    """§29, and the reason this sprint has no network scan in it.

    Every address discovery touches must be one the owner installed a runtime on (loopback)
    or one they named themselves. Finding a model server by looking for it is how Thursday
    would end up talking to a machine nobody authorised.
    """
    client = RecordingClient(routes={DEFAULT_ENDPOINT[RuntimeKind.OLLAMA]: OLLAMA_TAGS})
    await discover(client=client)

    assert client.asked, "discovery made no requests at all"
    assert client.hosts == {"127.0.0.1"}, client.hosts


async def test_a_configured_endpoint_is_contacted_and_a_guessed_one_is_not():
    """Naming an endpoint is the owner saying so, which is the whole difference between
    configuration and discovery."""
    client = RecordingClient(routes={"http://gpu-box.lan:11434": OLLAMA_TAGS})
    found = await discover(
        configured={RuntimeKind.OLLAMA: "http://gpu-box.lan:11434"}, client=client
    )

    assert [r.kind for r in found] == [RuntimeKind.OLLAMA]
    assert "gpu-box.lan" in client.hosts
    # And nothing adjacent to it: no sweep of the subnet it revealed.
    assert client.hosts <= {"127.0.0.1", "gpu-box.lan"}


def test_a_remote_runtime_is_refused_at_construction_not_at_request_time():
    """A runtime object that exists is one something will eventually call. Checking the
    address when it is built beats checking it at every call site that must remember to."""
    with pytest.raises(UnsafeEndpoint, match="not loopback"):
        HttpRuntime(RuntimeKind.OLLAMA, "http://192.168.1.50:11434")

    # Explicitly allowed is a different act, and it is what `configured` uses.
    assert HttpRuntime(
        RuntimeKind.OLLAMA, "http://192.168.1.50:11434", allow_remote=True
    ).endpoint.endswith(":11434")


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        ("http://127.0.0.1:11434", True),
        ("http://localhost:1234", True),
        ("http://[::1]:8080", True),
        ("http://192.168.1.5:11434", False),
        ("https://api.example.com", False),
        # The shapes somebody reaches for when they want this check to pass.
        ("http://127.0.0.1.example.com", False),
        ("http://evil.com#127.0.0.1", False),
    ],
)
def test_loopback_is_decided_by_the_parsed_host(endpoint, expected):
    assert is_loopback(endpoint) is expected


# --------------------------------------------------------------------------- what is found


async def test_models_are_listed_from_a_runtime_that_answers():
    client = RecordingClient(routes={DEFAULT_ENDPOINT[RuntimeKind.OLLAMA]: OLLAMA_TAGS})
    manager = LocalModelManager(client=client)
    models = await manager.refresh()

    assert {m.name for m in models} == {"llama3:8b", "llava:13b", "nomic-embed-text"}
    assert {m.runtime for m in models} == {RuntimeKind.OLLAMA}


async def test_a_machine_with_no_runtime_reports_nothing_rather_than_failing():
    """The common case, and this container's own case. Absence is an answer."""
    manager = LocalModelManager(client=RecordingClient(fail={"http://127.0.0.1"}))
    assert await manager.refresh() == []
    assert manager.capabilities() == set()


async def test_one_broken_runtime_does_not_hide_the_models_on_a_working_one():
    """A single try around the whole loop would lose every model on every other server the
    moment one of them started returning nonsense."""

    class HalfBroken(RecordingClient):
        async def get(self, url: str):
            if url.startswith(DEFAULT_ENDPOINT[RuntimeKind.LM_STUDIO]):
                raise RuntimeError("this server is up and answering rubbish")
            return await super().get(url)

    client = HalfBroken(routes={DEFAULT_ENDPOINT[RuntimeKind.OLLAMA]: OLLAMA_TAGS})
    models = await LocalModelManager(client=client).refresh()
    assert {m.name for m in models} == {"llama3:8b", "llava:13b", "nomic-embed-text"}


async def test_two_runtimes_on_one_machine_are_both_reported():
    """§2's example has both Ollama and LM Studio on one box."""
    client = RecordingClient(
        routes={
            DEFAULT_ENDPOINT[RuntimeKind.OLLAMA]: OLLAMA_TAGS,
            DEFAULT_ENDPOINT[RuntimeKind.LM_STUDIO]: LM_STUDIO_MODELS,
        }
    )
    manager = LocalModelManager(client=client)
    await manager.refresh()
    assert {r.kind for r in manager.runtimes} == {RuntimeKind.OLLAMA, RuntimeKind.LM_STUDIO}


# --------------------------------------------------------------------------- what it is for


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("llama3:8b", ModelKind.LLM),
        ("llava:13b", ModelKind.VISION),
        ("qwen2.5-vl-7b", ModelKind.VISION),
        ("moondream", ModelKind.VISION),
        ("nomic-embed-text", ModelKind.EMBEDDING),
        ("bge-reranker-v2", ModelKind.RERANK),
        ("whisper-large-v3", ModelKind.STT),
        ("piper-en-us", ModelKind.TTS),
        ("qwen2.5-coder-7b", ModelKind.CODE),
    ],
)
def test_a_models_purpose_is_guessed_from_its_name(name, expected):
    """Crude, and the docstring says so: the runtimes do not report what a model is for. A
    wrong guess sends an embedding request to a chat model and fails at the point of use,
    which is visible — unlike a wrong guess that silently returns something plausible."""
    assert kind_of(name) is expected


def test_the_runtimes_own_hint_beats_the_name():
    """Ollama reports `families`, and a model whose families include clip handles images
    whatever somebody called it."""
    assert kind_of("some-private-build", families=["llama", "clip"]) is ModelKind.VISION


async def test_capabilities_are_derived_from_installed_models_not_configured():
    """§42. A node that advertises `ai.vision` from a config file and holds no vision model
    is a node the router will send vision work to, and the lie surfaces at the point of use."""
    client = RecordingClient(routes={DEFAULT_ENDPOINT[RuntimeKind.OLLAMA]: OLLAMA_TAGS})
    manager = LocalModelManager(client=client)
    await manager.refresh()

    assert manager.capabilities() == {"ai.llm", "ai.vision", "ai.embedding"}


def test_the_capability_names_slot_into_the_existing_prefix_walk():
    """ADR 0007's lookup, unchanged. A node advertising `ai` supports all of them; one
    advertising `ai.embedding` supports exactly that."""
    from thursday_shared.models import DeviceCapabilities

    everything = DeviceCapabilities.of("ai")
    assert everything.supports("ai.vision") and everything.supports("ai.embedding")

    narrow = DeviceCapabilities.of("ai.embedding")
    assert narrow.supports("ai.embedding")
    assert not narrow.supports("ai.vision")


# --------------------------------------------------------------------------- the hard floor


def test_a_model_that_will_not_fit_is_not_offered_however_idle_the_machine():
    laptop = ComputeProfile(ram_bytes=16 * GIB, vram_bytes=0)
    workstation = ComputeProfile(ram_bytes=64 * GIB, vram_bytes=24 * GIB)
    big_vision = ModelDescriptor(
        name="llava:34b", kind=ModelKind.VISION, required_vram_bytes=20 * GIB
    )

    assert big_vision.fits(workstation) is True
    assert big_vision.fits(laptop) is False


def test_an_integrated_gpu_with_no_measurable_vram_is_not_a_gpu_to_route_to():
    """§17 puts vision work on the RTX box and not on the laptop. Keying `has_gpu` on the
    name would put it on anything that reports a name, integrated chips included."""
    assert ComputeProfile(gpu_name="Intel UHD Graphics", vram_bytes=0).has_gpu is False
    assert ComputeProfile(gpu_name="NVIDIA RTX 4090", vram_bytes=24 * GIB).has_gpu is True


def test_hardware_probing_survives_a_machine_that_answers_nothing(monkeypatch):
    """A node that refuses to start because it could not read its own VRAM is a node that
    does nothing, on a machine that can still open files and run commands."""
    monkeypatch.setattr(local_manager.shutil, "which", lambda _: None)
    profile = LocalModelManager().profile()
    assert profile.vram_bytes == 0
    assert profile.gpu_name == ""
    assert profile.platform, "the platform is knowable without any optional dependency"


# --------------------------------------------------------------------------- what is absent


def test_nothing_in_the_discovery_path_can_download_a_model():
    """§39, kept the way §120 was kept in the updater: by having nowhere to break it.

    Install and remove are not here-but-guarded, they are absent. They will arrive as device
    actions with policies of their own, because approval in this system means the Permission
    Engine, and a manager method is not something the engine can authorise.

    An AST walk, not a text scan. The first version of this searched the source for "install"
    and failed on the *docstring explaining why install is absent* — the same mistake the §120
    check made in Sprint 46, where a scan for "curl" matched a comment. Prose about a
    dangerous thing is not the dangerous thing; only a definition or a call is.
    """
    import ast

    defined: set[str] = set()
    called: set[str] = set()
    for module in (local_manager, runtimes):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                defined.add(node.name.lower())
            elif isinstance(node, ast.Call):
                target = node.func
                name = getattr(target, "attr", None) or getattr(target, "id", "")
                called.add(str(name).lower())

    forbidden = ("install", "download", "pull", "snapshot_download", "hf_hub_download")
    for word in forbidden:
        assert not any(word in name for name in defined), f"defines {word}"
        assert not any(word in name for name in called), f"calls {word}"

    assert not hasattr(LocalModelManager, "install_model")
    assert not hasattr(LocalModelManager, "remove_model")


def test_discovery_only_ever_issues_reads():
    """The other half of the same guarantee, and the one a future edit is likelier to break.

    A runtime adapter that grew a `post` would be able to start a pull on Ollama without
    anything named "install" appearing anywhere. So: the only HTTP verb this code uses is
    GET.
    """
    import ast

    verbs: set[str] = set()
    for module in (local_manager, runtimes):
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            verb = getattr(node.func, "attr", "") if isinstance(node, ast.Call) else ""
            if verb in {"get", "post", "put", "delete", "patch", "request"}:
                verbs.add(verb)

    assert verbs <= {"get"}, f"discovery issues more than reads: {verbs}"


async def test_the_offline_adapter_answers_rather_than_raising():
    """ADR 0001 — every port has a real adapter and an offline one."""
    nothing = NoRuntime()
    assert await nothing.available() is False
    assert await nothing.models() == []


def test_capabilities_for_is_a_set_so_duplicate_models_do_not_duplicate_capability():
    models = [
        ModelDescriptor(name="a", kind=ModelKind.LLM),
        ModelDescriptor(name="b", kind=ModelKind.LLM),
    ]
    assert capabilities_for(models) == {"ai.llm"}


# --------------------------------------------------------------------------- acceptance


def test_thursday_lists_local_models_without_anybody_configuring_them(client, container):
    """The sprint's acceptance criterion, end to end.

    A node connects, reports what discovery found on it, and the owner can ask Thursday
    where it can think — with nothing written in a configuration file. The inventory travels
    HELLO → hub → `GET /devices/compute`, so this fails if any link in that chain drops it.
    """
    import json

    from thursday_shared.compute import ComputeProfile, ModelDescriptor, ModelKind, RuntimeKind
    from thursday_shared.models import DeviceCapabilities, DeviceTelemetry
    from thursday_shared.protocol import Hello

    models = [
        ModelDescriptor(
            name="llava:13b",
            kind=ModelKind.VISION,
            runtime=RuntimeKind.OLLAMA,
            required_vram_bytes=8 * GIB,
        ),
        ModelDescriptor(name="nomic-embed-text", kind=ModelKind.EMBEDDING),
    ]
    hello = Hello(
        device_id=new_id(),
        name="GPU-PC",
        os="Windows",
        capabilities=DeviceCapabilities.of("app.open").grant(*capabilities_for(models)),
        telemetry=DeviceTelemetry(),
        compute=ComputeProfile(gpu_name="NVIDIA RTX 4090", vram_bytes=24 * GIB, ram_bytes=64 * GIB),
        models=models,
        nonce="hello-compute",
    )

    with client.websocket_connect("/api/v1/device") as ws:
        ws.send_text(hello.model_dump_json())
        assert json.loads(ws.receive_text())["type"] == "WELCOME"

        listed = client.get("/api/v1/devices/compute").json()["devices"]

    machine = next(d for d in listed if d["name"] == "GPU-PC")
    assert machine["can_run_models"] is True
    assert machine["compute"]["gpu_name"] == "NVIDIA RTX 4090"
    assert {m["name"] for m in machine["models"]} == {"llava:13b", "nomic-embed-text"}
    assert machine["ai_capabilities"] == ["ai.embedding", "ai.vision"]


def test_a_node_that_reports_no_inventory_is_listed_as_unable_rather_than_omitted(
    client, container
):
    """ "This machine cannot run models" and "this machine does not exist" are different
    answers to "where can Thursday think?", and only one of them is true for a laptop with
    no runtime installed."""
    import json

    from thursday_shared.models import DeviceCapabilities, DeviceTelemetry
    from thursday_shared.protocol import Hello

    hello = Hello(
        device_id=new_id(),
        name="Bare-Laptop",
        os="Linux",
        capabilities=DeviceCapabilities.of("app.open"),
        telemetry=DeviceTelemetry(),
        nonce="hello-bare",
    )
    with client.websocket_connect("/api/v1/device") as ws:
        ws.send_text(hello.model_dump_json())
        assert json.loads(ws.receive_text())["type"] == "WELCOME"
        listed = client.get("/api/v1/devices/compute").json()["devices"]

    machine = next(d for d in listed if d["name"] == "Bare-Laptop")
    assert machine["can_run_models"] is False
    assert machine["compute"] is None
    assert machine["models"] == []
