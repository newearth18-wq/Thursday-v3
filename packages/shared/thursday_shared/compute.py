"""What a machine can run, and what it is running now (ADDENDUM §3–§6, §42, §51).

Thursday's model layer used to answer one question — *which tier* — and a tier is a property
of a model, not of a machine. This module adds the vocabulary for the other half: **which
machine**, with what hardware, holding which models, under what load.

Three types, and the split between them is deliberate:

``ComputeProfile`` is what a machine *has* — cores, memory, a GPU, VRAM. It changes when
somebody installs hardware, which is to say almost never.

``ComputeLoad`` is what a machine is *doing* — CPU and GPU utilisation, free memory, queue
depth, whether it is on battery. It changes every few seconds, so it is reported with the
heartbeat and kept in Redis (§51) rather than in the database.

``ModelDescriptor`` is one model on one runtime on one machine. Its requirements are stated
in bytes rather than in adjectives, because "large" is not something a router can compare
against 16 GB of free VRAM.

Nothing here talks to a model or a device. These are the nouns; the runtimes that produce
them live in `thursday_models.runtimes`, and the router that consumes them in
`thursday_core.compute_router`.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class _Compute(BaseModel):
    """The same configuration `thursday_shared.models.Base` carries, declared here instead.

    Not imported from there, deliberately: `models.DeviceSummary` needs these types, so
    importing `Base` would make the two modules import each other. Two lines of duplication
    is the cheaper side of that trade, and every class below restates the config anyway.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


#: One gibibyte, because every size here is in bytes and the arithmetic is easier to read
#: against a named constant than against 1073741824.
GIB = 1024**3


class RuntimeKind(StrEnum):
    """The local inference servers §4 names, plus the generic case.

    ``OPENAI_COMPATIBLE`` is not a vendor — it is the shape of an HTTP API that half a dozen
    servers speak. Naming it separately means a runtime nobody has heard of yet is supported
    by pointing configuration at it, rather than by writing an adapter.
    """

    OLLAMA = "ollama"
    LM_STUDIO = "lm_studio"
    LLAMA_CPP = "llama_cpp"
    VLLM = "vllm"
    OPENAI_COMPATIBLE = "openai_compatible"
    #: Not a real runtime: what `detect` returns on a machine with none, so callers get an
    #: object that answers honestly rather than a None they have to remember to check.
    NONE = "none"


class ModelKind(StrEnum):
    """§6. What a model is *for*, which is what routing actually selects on."""

    LLM = "llm"
    REASONING = "reasoning"
    VISION = "vision"
    EMBEDDING = "embedding"
    STT = "stt"
    TTS = "tts"
    OCR = "ocr"
    OBJECT_DETECTION = "object_detection"
    RERANK = "rerank"
    CODE = "code"
    CLASSIFIER = "classifier"


class ModelState(StrEnum):
    """§22. A model that is loaded answers in milliseconds; one that is not may take a
    minute to page in from disk, which is a routing input rather than a detail."""

    LOADED = "LOADED"
    LOADING = "LOADING"
    UNLOADED = "UNLOADED"


#: §42. The capability namespace a node advertises, mapped from what it actually holds.
#: These slot into the existing prefix-walking `DeviceCapabilities` (ADR 0007), so a node
#: advertising `ai` supports every one of them and a node advertising `ai.embedding`
#: supports exactly one — no new matching rule was needed for any of this.
CAPABILITY_OF: dict[ModelKind, str] = {
    ModelKind.LLM: "ai.llm",
    ModelKind.REASONING: "ai.reasoning",
    ModelKind.VISION: "ai.vision",
    ModelKind.EMBEDDING: "ai.embedding",
    ModelKind.STT: "ai.stt",
    ModelKind.TTS: "ai.tts",
    ModelKind.OCR: "ai.ocr",
    ModelKind.OBJECT_DETECTION: "ai.object_detection",
    ModelKind.RERANK: "ai.rerank",
    ModelKind.CODE: "ai.code",
    ModelKind.CLASSIFIER: "ai.classifier",
}


class ComputeProfile(_Compute):
    """The hardware §3 asks each machine to report. Static, or near enough."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    cpu_cores: int = 0
    ram_bytes: int = 0
    #: Free-text because "RTX 4090" is for a person to read, not for the router to parse.
    #: The router compares `vram_bytes`, which is a number.
    gpu_name: str = ""
    vram_bytes: int = 0
    has_npu: bool = False
    disk_free_bytes: int = 0
    platform: str = ""

    @property
    def has_gpu(self) -> bool:
        """A GPU with no memory the router can measure is not a GPU it can route to.

        Deliberately keyed on VRAM rather than on the name: an integrated GPU reports a name
        too, and routing a vision model to it because a string was non-empty is exactly the
        mistake §17 describes (laptop with integrated GPU should not get the vision work).
        """
        return self.vram_bytes > 0


class ComputeLoad(_Compute):
    """§18, §51. What the machine is doing right now, reported with the heartbeat."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    cpu_percent: float = 0.0
    ram_free_bytes: int = 0
    gpu_percent: float = 0.0
    vram_free_bytes: int = 0
    #: How many AI jobs this node has accepted and not finished.
    queue_depth: int = 0
    #: §19. Power-aware routing needs to know, and `None` means "no battery" (a desktop),
    #: which is not the same as "battery unknown".
    on_battery: bool | None = None
    battery_percent: float | None = None
    #: §18. A thermally throttled machine is one to route *away* from even when its
    #: utilisation looks low, because low utilisation is what throttling produces.
    thermal_throttling: bool = False


class ModelDescriptor(_Compute):
    """One model, on one runtime, on one machine (§5)."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    name: str
    kind: ModelKind = ModelKind.LLM
    runtime: RuntimeKind = RuntimeKind.NONE
    context_length: int = 0
    size_bytes: int = 0
    #: What it needs to run, which is not what it takes on disk. A quantised 7B model is
    #: ~4 GB on disk and wants rather more than that resident.
    required_ram_bytes: int = 0
    required_vram_bytes: int = 0
    state: ModelState = ModelState.UNLOADED
    supports_tools: bool = False
    supports_vision: bool = False
    #: Set by the benchmark sprint; zero means "never measured", which the router must treat
    #: as unknown rather than as slow.
    tokens_per_second: float = 0.0
    tags: tuple[str, ...] = Field(default_factory=tuple)

    @property
    def capability(self) -> str:
        return CAPABILITY_OF[self.kind]

    def fits(self, profile: ComputeProfile) -> bool:
        """Whether this machine could run this model at all.

        A separate question from whether it *should* right now — that one needs load, and
        lives in the compute router. This is the hard floor: a model wanting 16 GB of VRAM
        on a machine with 8 GB will not run, however idle the machine is.
        """
        if self.required_vram_bytes and self.required_vram_bytes > profile.vram_bytes:
            return False
        return not (self.required_ram_bytes and self.required_ram_bytes > profile.ram_bytes)


def capabilities_for(models: list[ModelDescriptor]) -> set[str]:
    """The capability set a node should advertise, derived from what it actually holds.

    Derived rather than configured, on purpose. A node that advertises `ai.vision` in a
    config file and has no vision model is a node the router will send vision work to, and
    the failure arrives at the point of use rather than at the point of the lie.
    """
    return {model.capability for model in models}
