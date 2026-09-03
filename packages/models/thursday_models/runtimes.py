"""Local inference runtimes, and how Thursday finds them (ADDENDUM §4, §29, §39, §41).

§4 lists Ollama, LM Studio, llama.cpp, vLLM and "local OpenAI-compatible endpoint". The last
one is the interesting entry: it is not a vendor but the *shape* of an HTTP API that most of
the others also speak, so three of the five adapters here are one adapter with different
defaults, and a runtime nobody has heard of yet is supported by pointing configuration at it.

**Discovery does not scan, and that is the central decision.**

The obvious implementation of "find the local AI" sweeps the LAN for open inference ports.
It would work, and it is precisely what §29 forbids Thursday to rely on: local AI servers
must not be publicly reachable and must accept only localhost or a trusted LAN. A discovery
process that hunts for unauthenticated model servers is building the map an attacker wants,
on the owner's own network, and it would happily find and route work to a neighbour's
misconfigured server.

So discovery probes exactly two things: **loopback**, where a runtime the owner installed on
this machine lives, and **endpoints named in configuration**. Nothing else is ever contacted.
A GPU box down the hall is reached by running a Thursday node on it — which pairs, signs its
HELLO and can be revoked — not by finding its port. The acceptance criterion for this sprint
says "without manual configuration *where safe*", and this is where that clause lands.

**Discovery never installs.** §39 says a large model download must show the model, its size,
its source and the disk it needs before anything is fetched, and §41 puts install and remove
behind approval. Nothing in this module downloads: `discover` lists what is already there.
The install path is deliberately absent rather than present-and-guarded, because a code path
that can download is one somebody later calls without the guard.
"""

from __future__ import annotations

from typing import Any, Protocol
from urllib.parse import urlsplit

from thursday_core.logging import get_logger
from thursday_shared.compute import GIB, ModelDescriptor, ModelKind, ModelState, RuntimeKind

log = get_logger(__name__)

#: Where each runtime listens when somebody installs it and changes nothing. Loopback only —
#: see the module docstring for why there is no broadcast, no mDNS and no port sweep.
DEFAULT_ENDPOINT: dict[RuntimeKind, str] = {
    RuntimeKind.OLLAMA: "http://127.0.0.1:11434",
    RuntimeKind.LM_STUDIO: "http://127.0.0.1:1234",
    RuntimeKind.LLAMA_CPP: "http://127.0.0.1:8080",
    RuntimeKind.VLLM: "http://127.0.0.1:8000",
}

#: Hosts a runtime may sit on without being explicitly configured.
LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})

#: Substrings that identify what a model is *for*. Crude, and honest about it: the runtimes
#: do not report a model's purpose, so this reads the name. A wrong guess sends an embedding
#: request to a chat model, which fails loudly at the point of use rather than silently.
_KIND_HINTS: tuple[tuple[str, ModelKind], ...] = (
    ("embed", ModelKind.EMBEDDING),
    ("rerank", ModelKind.RERANK),
    ("whisper", ModelKind.STT),
    ("piper", ModelKind.TTS),
    ("tts", ModelKind.TTS),
    ("llava", ModelKind.VISION),
    ("vision", ModelKind.VISION),
    ("-vl", ModelKind.VISION),
    ("moondream", ModelKind.VISION),
    ("ocr", ModelKind.OCR),
    ("yolo", ModelKind.OBJECT_DETECTION),
    ("coder", ModelKind.CODE),
    ("code", ModelKind.CODE),
    ("reason", ModelKind.REASONING),
)


class UnsafeEndpoint(ValueError):
    """A runtime address that was neither loopback nor configured."""


class AIRuntime(Protocol):
    """One local inference server."""

    kind: RuntimeKind
    endpoint: str

    async def available(self) -> bool: ...

    async def models(self) -> list[ModelDescriptor]: ...


class NoRuntime:
    """The offline adapter (ADR 0001), and what `detect` returns on a bare machine.

    Answers honestly rather than raising: "there is no local runtime here" is a fact the
    compute router needs to route around, not an error it should have to catch.
    """

    kind = RuntimeKind.NONE
    endpoint = ""

    async def available(self) -> bool:
        return False

    async def models(self) -> list[ModelDescriptor]:
        return []


class HttpRuntime:
    """The real adapter, for every runtime that speaks HTTP — which is all of them.

    One class rather than four because the differences are two strings (the path that lists
    models, and where the name sits in the response), and four near-identical classes is
    four places for a fix to be applied three times.
    """

    def __init__(
        self,
        kind: RuntimeKind,
        endpoint: str | None = None,
        *,
        client: Any = None,
        timeout: float = 5.0,
        allow_remote: bool = False,
    ) -> None:
        self.kind = kind
        self.endpoint = (endpoint or DEFAULT_ENDPOINT.get(kind, "")).rstrip("/")
        self._client = client
        self._timeout = timeout
        if not allow_remote and self.endpoint and not is_loopback(self.endpoint):
            # Refused at construction, not at request time. A runtime object that exists is
            # one something will eventually call, and "we check before sending" is a
            # promise that outlives the call site that made it.
            raise UnsafeEndpoint(
                f"{self.endpoint} is not loopback; a runtime on another machine is reached "
                "through a paired Thursday node, or by naming it in configuration"
            )

    @property
    def _list_path(self) -> str:
        return "/api/tags" if self.kind is RuntimeKind.OLLAMA else "/v1/models"

    async def available(self) -> bool:
        """Whether this runtime answers. Never raises — absence is the common case."""
        try:
            return (await self._get(self._list_path)) is not None
        except Exception as exc:
            log.debug("runtime_unavailable", runtime=str(self.kind), error=str(exc))
            return False

    async def models(self) -> list[ModelDescriptor]:
        payload = await self._get(self._list_path)
        if payload is None:
            return []
        rows = payload.get("models") if self.kind is RuntimeKind.OLLAMA else payload.get("data")
        return [d for row in rows or [] if (d := self._describe(row)) is not None]

    def _describe(self, row: Any) -> ModelDescriptor | None:
        if not isinstance(row, dict):
            return None
        name = str(row.get("name") or row.get("model") or row.get("id") or "").strip()
        if not name:
            return None
        size = int(row.get("size") or 0)
        # Read once and then narrowed. The version that called `.get("details")` twice
        # checked one value and used another — harmless with a plain dict, wrong the moment
        # the mapping is anything lazier, and mypy was right to object.
        raw = row.get("details")
        details: dict = raw if isinstance(raw, dict) else {}
        return ModelDescriptor(
            name=name,
            kind=kind_of(name, families=details.get("families")),
            runtime=self.kind,
            size_bytes=size,
            # The runtimes do not report what a model needs resident, so this estimates from
            # what it takes on disk plus room to work. An estimate that is stated as one:
            # the benchmark sprint replaces it with a measurement.
            required_ram_bytes=size + GIB // 2 if size else 0,
            state=ModelState.UNLOADED,
        )

    async def _get(self, path: str) -> dict | None:
        client = self._client
        if client is None:
            import httpx

            async with httpx.AsyncClient(timeout=self._timeout) as fresh:
                response = await fresh.get(f"{self.endpoint}{path}")
                return response.json() if response.status_code == 200 else None
        response = await client.get(f"{self.endpoint}{path}")
        return response.json() if response.status_code == 200 else None


def is_loopback(endpoint: str) -> bool:
    host = (urlsplit(endpoint).hostname or "").lower()
    return host in LOOPBACK


def kind_of(name: str, *, families: Any = None) -> ModelKind:
    """Guess what a model is for from its name, and from the runtime's own hints.

    `families` is Ollama's field, and it is better evidence than the name when present — a
    model whose families include `clip` handles images whatever it is called.
    """
    if isinstance(families, list) and any("clip" in str(f).lower() for f in families):
        return ModelKind.VISION
    lowered = name.lower()
    for hint, kind in _KIND_HINTS:
        if hint in lowered:
            return kind
    return ModelKind.LLM


async def discover(
    *,
    configured: dict[RuntimeKind, str] | None = None,
    client: Any = None,
) -> list[AIRuntime]:
    """Every local runtime that is actually answering, loopback and configured only.

    Returns the runtimes, not the models: listing models is a second request per runtime and
    the caller may only want to know whether anything is there. `LocalModelManager` does
    both and is what the container wires.
    """
    candidates: list[AIRuntime] = []
    for kind, endpoint in DEFAULT_ENDPOINT.items():
        candidates.append(HttpRuntime(kind, endpoint, client=client))
    for kind, endpoint in (configured or {}).items():
        # Configured endpoints may be remote: naming one is the owner saying so, which is
        # the difference between configuration and discovery.
        candidates.append(HttpRuntime(kind, endpoint, client=client, allow_remote=True))

    found = [runtime for runtime in candidates if await runtime.available()]
    log.info("runtimes_discovered", found=[str(r.kind) for r in found])
    return found
