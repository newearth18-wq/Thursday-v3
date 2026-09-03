"""Ports.

Structural protocols only, so an adapter never has to import Thursday to satisfy one.
Core code depends on these names and nothing else; concrete providers are built in
``thursday_core.container`` from settings (§4.4, §78).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import Any, Protocol, TypeVar, runtime_checkable
from uuid import UUID

from thursday_shared.enums import ModelTier
from thursday_shared.models import (
    AgentResult,
    AgentSpec,
    ContextPackage,
    DeviceAction,
    DeviceActionResult,
    DeviceCapabilities,
    DeviceTelemetry,
    Event,
    HealthStatus,
    JobContract,
    LLMRequest,
    LLMResponse,
    MemoryQuery,
    MemoryRecord,
    MemoryWrite,
    ToolCall,
    ToolResult,
    ToolSpec,
)

T = TypeVar("T")


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    tier: ModelTier
    local: bool

    async def complete(self, request: LLMRequest) -> LLMResponse: ...
    def stream(self, request: LLMRequest) -> AsyncIterator[str]: ...
    async def health(self) -> HealthStatus: ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    dimensions: int

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


@runtime_checkable
class VectorProvider(Protocol):
    async def upsert(self, items: Sequence[tuple[UUID, list[float], dict[str, Any]]]) -> None: ...
    async def search(
        self, vector: list[float], *, k: int, where: dict[str, Any] | None = None
    ) -> list[tuple[UUID, float]]: ...
    async def delete(self, ids: Sequence[UUID]) -> None: ...


@runtime_checkable
class MemoryProvider(Protocol):
    async def write(self, record: MemoryWrite) -> MemoryRecord | None: ...
    async def recall(self, query: MemoryQuery) -> list[MemoryRecord]: ...
    async def get(self, memory_id: UUID) -> MemoryRecord | None: ...
    async def supersede(self, old_id: UUID, new: MemoryWrite) -> MemoryRecord: ...
    async def forget(self, memory_id: UUID) -> None: ...


@runtime_checkable
class STTProvider(Protocol):
    name: str
    local: bool

    async def transcribe(self, audio: bytes, *, language: str | None = None) -> str: ...


@runtime_checkable
class TTSProvider(Protocol):
    name: str
    local: bool

    async def synthesize(self, text: str, *, mode: str, voice: str | None = None) -> bytes: ...


@runtime_checkable
class WakeWordProvider(Protocol):
    keyword: str

    async def detect(self, audio: bytes) -> bool: ...


@runtime_checkable
class ToolProvider(Protocol):
    spec: ToolSpec

    async def run(self, call: ToolCall, ctx: ExecutionContext) -> ToolResult: ...


@runtime_checkable
class AgentProvider(Protocol):
    spec: AgentSpec

    async def run(self, contract: JobContract, ctx: ExecutionContext) -> AgentResult: ...


@runtime_checkable
class DeviceProvider(Protocol):
    """Core-side handle to one device node."""

    device_id: UUID
    name: str
    capabilities: DeviceCapabilities

    async def invoke(self, action: DeviceAction) -> DeviceActionResult: ...
    async def ping(self) -> DeviceTelemetry: ...


@runtime_checkable
class VisionProvider(Protocol):
    async def analyze(self, frame: bytes, request: dict[str, Any]) -> dict[str, Any]: ...


@runtime_checkable
class SecretVault(Protocol):
    """§35. ``use`` is the only way a raw value is ever materialised, and never to an LLM."""

    async def put(self, handle: str, value: str) -> None: ...
    async def has(self, handle: str) -> bool: ...
    async def use(self, handle: str, fn: Callable[[str], Awaitable[T]]) -> T: ...
    async def delete(self, handle: str) -> None: ...


@runtime_checkable
class EventBus(Protocol):
    async def publish(self, event: Event) -> None: ...
    def subscribe(self, pattern: str, handler: Callable[[Event], Awaitable[None]]) -> None: ...


@runtime_checkable
class ExecutionContext(Protocol):
    """Everything an agent or tool is allowed to reach. Deliberately narrow."""

    task_id: UUID | None
    trace_id: str
    context: ContextPackage | None

    async def call_tool(self, call: ToolCall) -> ToolResult: ...
    async def recall(self, query: MemoryQuery) -> list[MemoryRecord]: ...
    async def remember(self, write: MemoryWrite) -> MemoryRecord | None: ...
    async def think(self, request: LLMRequest) -> LLMResponse: ...
    async def emit(self, event: Event) -> None: ...
