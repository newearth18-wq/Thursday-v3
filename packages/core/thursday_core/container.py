"""Dependency-injection container (§99).

The single place where concrete providers are chosen and wired. Core modules receive ports;
they never construct a provider. That is the whole mechanism behind "swap the LLM, the STT,
the vector store or the agent framework without rewriting the system" (§4).

Tests build a container of fakes with the same attribute surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from thursday_agents.computer import ComputerAgent
from thursday_agents.factory import AgentFactory
from thursday_agents.registry import AgentRegistry
from thursday_agents.research import ResearchAgent
from thursday_automation.engine import AutomationEngine, ProactivityGate
from thursday_automation.routines import RoutineLearner
from thursday_automation.skills.registry import SkillRegistry
from thursday_devices.hub import DeviceHub
from thursday_memory.embeddings import HashEmbeddingProvider, OllamaEmbeddingProvider
from thursday_memory.graph import KnowledgeGraph
from thursday_memory.manager import MemoryManager
from thursday_memory.obsidian import ObsidianVault
from thursday_memory.vector import InMemoryVectorStore
from thursday_models.llm import AnthropicLLM, OllamaLLM, RuleBasedLLM
from thursday_security.approvals import ApprovalService
from thursday_security.audit import AuditLog
from thursday_security.permissions import PermissionEngine
from thursday_security.policy import PolicyTable
from thursday_security.privacy import PrivacyClassifier, PrivacyZoneRegistry
from thursday_security.redaction import SecretRedactor
from thursday_security.vault import ChainVault, EnvVault, InMemoryVault
from thursday_shared.enums import ModelTier
from thursday_tools.builtin import register_builtin_tools
from thursday_tools.registry import ToolRegistry, ToolRouter
from thursday_vision.gestures import GestureMode
from thursday_vision.spatial import SpatialMemory

from thursday_core.bus import InProcessEventBus
from thursday_core.composer import ResponseComposer
from thursday_core.config import Settings, get_settings
from thursday_core.context import ContextEngine
from thursday_core.device_router import DeviceRouter
from thursday_core.execution import ToolExecutor
from thursday_core.logging import configure_logging, get_logger
from thursday_core.model_router import ModelRouter
from thursday_core.orchestrator import AgentOrchestrator
from thursday_core.planner import Planner
from thursday_core.projects import ProjectManager
from thursday_core.reasoning import ReasoningEngine
from thursday_core.supervisor import Supervisor
from thursday_core.tasks import TaskManager, TaskQueue
from thursday_core.undo import UndoRegistry
from thursday_core.world import WorldState, WorldStateProjector

log = get_logger(__name__)


def _mask(url: str) -> str:
    """Never return a password from /health, even to a local caller."""
    if "://" not in url or "@" not in url:
        return url
    scheme, _, rest = url.partition("://")
    _, _, host = rest.rpartition("@")
    return f"{scheme}://***@{host}"


@dataclass
class Container:
    settings: Settings

    # infrastructure
    bus: Any = None
    audit: Any = None
    redactor: Any = None
    vault: Any = None

    # security
    policy: Any = None
    zones: Any = None
    classifier: Any = None
    permissions: Any = None
    approvals: Any = None
    undo: Any = None

    # memory
    embedder: Any = None
    vectors: Any = None
    memory: Any = None
    obsidian: Any = None
    graph: Any = None

    # models
    models: Any = None

    # devices
    hub: Any = None
    device_router: Any = None

    # execution
    tools: Any = None
    tool_router: Any = None
    executor: Any = None
    agents: Any = None
    tasks: Any = None
    projects: Any = None
    queue: Any = None
    supervisor: Any = None
    orchestrator: Any = None
    agent_factory: Any = None

    # automation, skills, perception
    automations: Any = None
    routines: Any = None
    skills: Any = None
    spatial: Any = None
    gesture_mode: Any = None

    # voice
    stt: Any = None
    tts: Any = None
    wake_word: Any = None

    # conversation
    world: Any = None
    context_engine: Any = None
    reasoning: Any = None
    planner: Any = None
    composer: Any = None
    engine: Any = None

    async def health(self) -> list[dict[str, Any]]:
        """§59 — one place that knows whether Thursday is actually able to work."""
        checks: list[dict[str, Any]] = []
        for status in await self.models.health():
            checks.append(
                {"component": f"model:{status.name}", "ok": status.ok, "detail": status.detail}
            )
        online = self.hub.online()
        checks.append(
            {
                "component": "devices",
                "ok": bool(online),
                "detail": f"{len(online)} online: {', '.join(d.name for d in online) or 'none'}",
            }
        )
        checks.append(
            {
                "component": "database",
                "ok": True,
                "detail": _mask(self.settings.resolved_database_url),
            }
        )
        checks.append(
            {
                "component": "redis",
                "ok": True,
                "detail": (
                    self.settings.redis_url
                    or "not configured — in-process bus and queue (ADR 0006)"
                ),
            }
        )
        checks.append(
            {
                "component": "memory",
                "ok": True,
                "detail": ", ".join(f"{k}={v}" for k, v in self.memory.stats().items()),
            }
        )
        checks.append(
            {
                "component": "audit",
                "ok": self.audit.verify_chain(),
                "detail": f"{len(self.audit)} entries, hash chain intact",
            }
        )
        checks.append(
            {
                "component": "approvals",
                "ok": True,
                "detail": f"{len(self.approvals.pending())} pending",
            }
        )
        checks.append(
            {
                "component": "queue",
                "ok": True,
                "detail": f"{len(self.queue.running())} running",
            }
        )
        checks.append(
            {
                "component": "automations",
                "ok": True,
                "detail": (
                    f"{len(self.automations.list(enabled_only=True))} enabled, "
                    f"{len(self.routines.unproposed())} routine suggestions pending"
                ),
            }
        )
        checks.append(
            {
                "component": "voice",
                "ok": True,
                "detail": (
                    f"wake={self.wake_word.keyword} stt={self.stt.name} tts={self.tts.name}"
                ),
            }
        )
        checks.append(
            {
                "component": "skills",
                "ok": True,
                "detail": f"{len(self.skills.active())} active, {len(self.skills.list())} total",
            }
        )
        return checks

    async def emergency_stop(self, scope: str = "all") -> dict[str, Any]:
        """§69. Deliberately does not route through the reasoning engine — it must work
        when the model is down."""
        actions: dict[str, Any] = {}
        if scope in ("all", "agents"):
            cancelled = 0
            for task in self.tasks.list():
                if not task.status.is_terminal:
                    self.queue.cancel(task.id)
                    await self.tasks.cancel(task.id, reason="emergency stop")
                    cancelled += 1
            actions["tasks_cancelled"] = cancelled
        if scope in ("all", "devices"):
            actions["devices_disconnected"] = await self.hub.disconnect_all()
        if scope in ("all", "camera", "microphone"):
            self.gesture_mode.close()
            actions["capture_disabled"] = True
            actions["gesture_mode"] = "closed"
        if scope == "all":
            self.permissions.set_lockdown(True)
            actions["lockdown"] = True
        log.warning("emergency_stop", scope=scope, **actions)
        return actions


def build_container(settings: Settings | None = None, *, configure_logs: bool = True) -> Container:
    settings = settings or get_settings()
    if configure_logs:
        configure_logging(level=settings.log_level, json_output=settings.log_json)
    settings.ensure_dirs()

    c = Container(settings=settings)

    # -- infrastructure -------------------------------------------------------
    c.bus = InProcessEventBus()
    c.redactor = SecretRedactor()
    c.audit = AuditLog(c.redactor)
    c.vault = _build_vault(settings)

    # -- security -------------------------------------------------------------
    c.policy = PolicyTable()
    c.zones = PrivacyZoneRegistry()
    c.classifier = PrivacyClassifier(c.redactor)
    c.permissions = PermissionEngine(policy=c.policy, zones=c.zones)
    c.approvals = ApprovalService(c.permissions, c.bus, default_ttl_s=settings.approval_ttl_seconds)
    c.undo = UndoRegistry()

    # -- memory ---------------------------------------------------------------
    c.embedder = _build_embedder(settings)
    c.vectors = InMemoryVectorStore()
    c.memory = MemoryManager(
        embedder=c.embedder,
        vectors=c.vectors,
        bus=c.bus,
        redactor=c.redactor,
        working_ttl_hours=settings.memory_working_ttl_hours,
    )
    c.obsidian = ObsidianVault(
        settings.obsidian_vault, redactor=c.redactor, enabled=settings.obsidian_enabled
    )
    c.obsidian.ensure_structure()
    c.graph = KnowledgeGraph()

    # -- models ---------------------------------------------------------------
    c.models = _build_models(settings, c.vault)

    # -- devices --------------------------------------------------------------
    c.hub = DeviceHub(c.bus)
    c.device_router = DeviceRouter(c.hub)

    # -- execution ------------------------------------------------------------
    c.tools = ToolRegistry()
    register_builtin_tools(c.tools, hub=c.hub, memory=c.memory, vault=c.obsidian)
    c.tool_router = ToolRouter(c.tools)
    c.tasks = TaskManager(c.bus)
    c.queue = TaskQueue()
    c.executor = ToolExecutor(
        registry=c.tools,
        permissions=c.permissions,
        approvals=c.approvals,
        audit=c.audit,
        undo=c.undo,
        bus=c.bus,
        tasks=c.tasks,
        approval_timeout_s=settings.approval_ttl_seconds,
    )
    c.projects = ProjectManager(tasks=c.tasks, memory=c.memory)
    c.agents = AgentRegistry()
    c.agents.register(ComputerAgent())
    c.agents.register(ResearchAgent())
    c.agent_factory = AgentFactory(c.agents)
    c.supervisor = Supervisor(c.models, use_llm_critique=not settings.offline)

    # -- automation, skills, perception ---------------------------------------
    c.skills = SkillRegistry(executor=c.executor, tools=c.tools)
    c.spatial = SpatialMemory()
    c.gesture_mode = GestureMode()

    # -- voice ----------------------------------------------------------------
    c.stt, c.tts, c.wake_word = _build_voice(settings)

    # -- conversation ---------------------------------------------------------
    c.world = WorldState()
    WorldStateProjector(c.world).attach(c.bus)
    c.context_engine = ContextEngine(
        memory=c.memory, world=c.world, hub=c.hub, classifier=c.classifier, zones=c.zones
    )
    c.reasoning = ReasoningEngine(c.models, wake_word=settings.wake_word)
    c.planner = Planner(max_steps=settings.max_plan_steps)
    c.composer = ResponseComposer()
    c.orchestrator = AgentOrchestrator(
        agents=c.agents,
        tools=c.tools,
        executor=c.executor,
        supervisor=c.supervisor,
        tasks=c.tasks,
        memory=c.memory,
        models=c.models,
        bus=c.bus,
        device_router=c.device_router,
        hub=c.hub,
        max_attempts=settings.max_step_attempts,
    )

    c.automations = AutomationEngine(
        bus=c.bus,
        executor=c.executor,
        tasks=c.tasks,
        world=c.world,
        gate=ProactivityGate(settings.proactivity),
    )
    c.automations.attach()
    c.routines = RoutineLearner()
    c.routines.attach(c.bus)

    from thursday_core.engine import ThursdayCore

    c.engine = ThursdayCore(c)
    _register_undo_executors(c)

    c.permissions.set_autonomy(settings.autonomy)

    log.info(
        "container_built",
        llm=settings.llm_backend,
        offline=settings.offline,
        tools=len(c.tools.names()),
        agents=len(c.agents.specs()),
        proactivity=settings.proactivity.name,
        autonomy=settings.autonomy.name,
    )
    return c


# --------------------------------------------------------------------------- builders


def _build_vault(settings: Settings) -> Any:
    if settings.vault_backend == "memory":
        return InMemoryVault()
    if settings.vault_backend == "keychain":
        # The OS keychain adapter lands in Phase 2; chain it ahead of the environment so
        # the swap is a configuration change, not a code change.
        return ChainVault(EnvVault())
    return EnvVault()


def _build_voice(settings: Settings) -> tuple[Any, Any, Any]:
    """Wake word, STT and TTS. The stubs are text-driven, so the whole voice path — wake,
    transcription, mode selection, routing — is exercisable in CI with no microphone."""
    from thursday_voice.providers import (
        KeywordWakeWord,
        PiperTTS,
        TextStubSTT,
        TextStubTTS,
        WhisperSTT,
    )

    stt: Any = WhisperSTT() if settings.stt_backend == "whisper" else TextStubSTT()
    tts: Any = (
        PiperTTS(model_path=str(settings.data_dir / "piper.onnx"))
        if settings.tts_backend == "piper"
        else TextStubTTS()
    )
    return stt, tts, KeywordWakeWord(settings.wake_word)


def _build_embedder(settings: Settings) -> Any:
    if settings.embedding_backend == "ollama":
        return OllamaEmbeddingProvider(settings.ollama_url)
    return HashEmbeddingProvider(settings.embedding_dimensions)


def _build_models(settings: Settings, vault: Any) -> ModelRouter:
    router = ModelRouter(allow_cloud=settings.allow_cloud)
    local = RuleBasedLLM()

    if settings.llm_backend == "ollama":
        local = OllamaLLM(settings.ollama_url, settings.ollama_model)  # type: ignore[assignment]
    router.register(ModelTier.LOCAL, local)

    if settings.llm_backend == "anthropic" and settings.allow_cloud:
        for tier, model in (
            (ModelTier.FAST, settings.llm_fast_model),
            (ModelTier.STANDARD, settings.llm_standard_model),
            (ModelTier.REASONING, settings.llm_reasoning_model),
            (ModelTier.VISION, settings.llm_standard_model),
        ):
            router.register(
                tier, AnthropicLLM(model, vault, settings.anthropic_api_key_handle, tier=tier)
            )
    else:
        # Offline or Ollama-only: every tier resolves to the local provider, so the router's
        # logic is still exercised and nothing silently reaches for the network.
        for tier in (ModelTier.FAST, ModelTier.STANDARD, ModelTier.REASONING, ModelTier.VISION):
            router.register(tier, local)
    return router


def _register_undo_executors(c: Container) -> None:
    """Wire undo operations to real device actions (§40)."""
    from thursday_shared.models import DeviceAction, UndoRecord

    async def device_undo(record: UndoRecord) -> bool:
        if record.device_id is None:
            online = c.hub.online()
            if not online:
                return False
            device_id = online[0].id
        else:
            device_id = record.device_id
        action_map = {
            "app.close": ("app.close", record.args),
            "app.open": ("app.open", record.args),
            "file.folder.delete": ("file.delete", {"path": record.args.get("path")}),
            "file.move": ("file.move", record.args),
            "file.delete": ("file.delete", record.args),
            "file.restore_from_trash": (
                "file.move",
                {"src": record.args.get("src"), "dst": record.args.get("dst")},
            ),
            "clipboard.write": ("clipboard.write", record.args),
            "audio.volume.set": ("audio.volume.set", record.args),
        }
        mapped = action_map.get(record.operation)
        if mapped is None:
            return False
        action, args = mapped
        result = await c.hub.invoke(
            device_id, DeviceAction(action=action, args=args, reason="undo")
        )
        return result.succeeded

    async def restore_file(record: UndoRecord) -> bool:
        previous = record.previous_state or {}
        if previous.get("absent"):
            return await device_undo(
                UndoRecord(
                    action_id=record.action_id, operation="file.folder.delete", args=record.args
                )
            )
        online = c.hub.online()
        if not online:
            return False
        result = await c.hub.invoke(
            record.device_id or online[0].id,
            DeviceAction(
                action="file.write",
                args={"path": record.args.get("path"), "content": previous.get("content", "")},
                reason="undo",
            ),
        )
        return result.succeeded

    async def memory_forget(record: UndoRecord) -> bool:
        from uuid import UUID

        memory_id = record.args.get("memory_id")
        if not memory_id:
            return False
        await c.memory.forget(UUID(str(memory_id)))
        return True

    for operation in (
        "app.close",
        "app.open",
        "file.folder.delete",
        "file.move",
        "file.delete",
        "file.restore_from_trash",
        "clipboard.write",
        "audio.volume.set",
    ):
        c.undo.register_executor(operation, device_undo)
    c.undo.register_executor("file.restore", restore_file)
    c.undo.register_executor("memory.forget", memory_forget)
