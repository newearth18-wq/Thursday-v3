# 5. Core Interfaces (Ports)

All in `thursday/shared/interfaces.py` as `typing.Protocol` — structural, so an adapter
never imports Thursday to satisfy one. Every port ships with (a) a real adapter and
(b) an offline/fake adapter used by tests.

```python
class LLMProvider(Protocol):
    name: str
    tier: ModelTier  # FAST | STANDARD | REASONING | VISION | LOCAL

    async def complete(self, req: LLMRequest) -> LLMResponse: ...
    def stream(self, req: LLMRequest) -> AsyncIterator[LLMChunk]: ...
    async def health(self) -> HealthStatus: ...


class STTProvider(Protocol):
    async def transcribe(self, audio: AudioChunk, *, language: str | None) -> Transcript: ...
    def stream(self, audio: AsyncIterator[AudioChunk]) -> AsyncIterator[Transcript]: ...


class TTSProvider(Protocol):
    async def synthesize(self, text: str, *, mode: VoiceMode, voice: str) -> AudioClip: ...


class WakeWordProvider(Protocol):
    def detect(self, frames: AsyncIterator[AudioChunk]) -> AsyncIterator[WakeEvent]: ...


class VectorProvider(Protocol):
    async def upsert(self, items: Sequence[VectorItem]) -> None: ...
    async def search(self, q: Sequence[float], *, k: int, flt: VectorFilter) -> list[VectorHit]: ...
    async def delete(self, ids: Sequence[UUID]) -> None: ...


class EmbeddingProvider(Protocol):
    dimensions: int

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class MemoryProvider(Protocol):  # layered store on top of VectorProvider + SQL
    async def write(self, rec: MemoryWrite) -> MemoryRecord | None: ...
    async def recall(self, q: MemoryQuery) -> list[MemoryRecord]: ...
    async def supersede(self, old_id: UUID, new: MemoryWrite) -> MemoryRecord: ...


class ToolProvider(Protocol):
    spec: ToolSpec  # name, caps, permission, cost, latency, risk, schemas

    async def run(self, call: ToolCall, ctx: ExecutionContext) -> ToolResult: ...


class AgentProvider(Protocol):
    spec: AgentSpec  # name, capabilities, tools, permissions, budget

    async def run(self, contract: JobContract, ctx: ExecutionContext) -> AgentResult: ...


class DeviceProvider(Protocol):  # core-side handle to a node
    device_id: UUID
    capabilities: DeviceCapabilities

    async def invoke(self, action: DeviceAction, *, timeout: float) -> DeviceActionResult: ...
    async def ping(self) -> DeviceTelemetry: ...


class VisionProvider(Protocol):
    async def analyze(self, frame: Frame, req: VisionRequest) -> VisionResult: ...


class SecretVault(Protocol):
    async def get(self, handle: str) -> SecretRef: ...  # never returns raw to an LLM
    async def use(self, handle: str, fn: Callable[[str], Awaitable[T]]) -> T: ...
    async def put(self, handle: str, value: str) -> None: ...


class EventBus(Protocol):
    async def publish(self, event: Event) -> None: ...
    def subscribe(self, pattern: str) -> AsyncIterator[Event]: ...
```

## 5.1 Data contracts (excerpt)

```python
class ContextPackage(BaseModel):
    turn: ConversationTurn
    history: list[ConversationTurn]  # windowed, not the whole transcript
    world: WorldStateSnapshot
    memories: list[MemoryRecord]  # retrieved, scored, deduped
    devices: list[DeviceSummary]
    screen: ScreenContext | None
    selection: SelectionContext | None
    project: ProjectSummary | None
    sensitivity: DataSensitivity
    budget: Budget


class Intent(BaseModel):
    kind: IntentKind  # ANSWER CHAT SEARCH DEVICE_ACTION FILE_OP ANALYZE CREATE
    # AUTOMATE RECALL STOP APPROVE CLARIFY
    objective: str
    entities: dict[str, Any]
    target_device: str | None
    needs_plan: bool
    confidence: float
    rationale: str


class JobContract(BaseModel):  # §17 — every agent gets one
    task_id: UUID
    step_id: UUID
    objective: str
    inputs: dict[str, Any]
    output_schema: dict[str, Any]
    success_criteria: list[str]
    permissions: PermissionSet
    deadline_s: float
    budget: Budget
    trace_id: str
```

## 5.2 The DI container

`thursday/core/container.py` builds every provider from `Settings` and exposes them as one
`Container`. Tests build a container of fakes with the same shape. Nothing anywhere calls a
provider constructor directly; that rule is what makes §4's "swap a provider without a
rewrite" true rather than aspirational.
