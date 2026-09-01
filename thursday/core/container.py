"""Dependency-injection container (§99).

The single place where concrete providers are chosen and wired. Core modules receive ports;
they never construct a provider. That is the whole mechanism behind "swap the LLM, the STT,
the vector store or the agent framework without rewriting the system" (§4).

Tests build a container of fakes with the same attribute surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from thursday.agents.computer import ComputerAgent
from thursday.agents.factory import AgentFactory
from thursday.agents.registry import AgentRegistry
from thursday.agents.research import ResearchAgent
from thursday.automation.engine import AutomationEngine, ProactivityGate
from thursday.automation.routines import RoutineLearner
from thursday.core.bus import InProcessEventBus
from thursday.core.composer import ResponseComposer
from thursday.core.config import Settings, get_settings
from thursday.core.context import ContextEngine
from thursday.core.device_router import DeviceRouter
from thursday.core.execution import ToolExecutor
from thursday.core.logging import configure_logging, get_logger
from thursday.core.model_router import ModelRouter
from thursday.core.orchestrator import AgentOrchestrator
from thursday.core.planner import Planner
from thursday.core.reasoning import ReasoningEngine
from thursday.core.supervisor import Supervisor
from thursday.core.tasks import TaskManager, TaskQueue
from thursday.core.undo import UndoRegistry
from thursday.core.world import WorldState, WorldStateProjector
from thursday.devices.hub import DeviceHub
from thursday.memory.embeddings import HashEmbeddingProvider, OllamaEmbeddingProvider
from thursday.memory.graph import KnowledgeGraph
from thursday.memory.manager import MemoryManager
from thursday.memory.obsidian import ObsidianVault
from thursday.memory.vector import InMemoryVectorStore
from thursday.providers.llm import AnthropicLLM, OllamaLLM, RuleBasedLLM
from thursday.security.approvals import ApprovalService
from thursday.security.audit import AuditLog
from thursday.security.permissions import PermissionEngine
from thursday.security.policy import PolicyTable
from thursday.security.privacy import PrivacyClassifier, PrivacyZoneRegistry
from thursday.security.redaction import SecretRedactor
from thursday.security.vault import ChainVault, EnvVault, InMemoryVault
from thursday.shared.enums import ModelTier
from thursday.skills.registry import SkillRegistry
from thursday.tools.builtin import register_builtin_tools
from thursday.tools.registry import ToolRegistry, ToolRouter
from thursday.vision.gestures import GestureMode
from thursday.vision.spatial import SpatialMemory

log = get_logger(__name__)


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
    c.agents = AgentRegistry()
    c.agents.register(ComputerAgent())
    c.agents.register(ResearchAgent())
    c.agent_factory = AgentFactory(c.agents)
    c.supervisor = Supervisor(c.models, use_llm_critique=not settings.offline)

    # -- automation, skills, perception ---------------------------------------
    c.skills = SkillRegistry(executor=c.executor, tools=c.tools)
    c.spatial = SpatialMemory()
    c.gesture_mode = GestureMode()

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

    from thursday.core.engine import ThursdayEngine

    c.engine = ThursdayEngine(c)
    _register_undo_executors(c)

    log.info(
        "container_built",
        llm=settings.llm_backend,
        offline=settings.offline,
        tools=len(c.tools.names()),
        agents=len(c.agents.specs()),
        proactivity=settings.proactivity.name,
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
    from thursday.shared.models import DeviceAction, UndoRecord

    async def device_undo(record: UndoRecord) -> bool:
        if record.device_id is None:
            online = c.hub.online()
            if not online:
                return False
            device_id = online[0].id
        else:
            device_id = record.device_id
        action_map = {
            "close_app": ("close_app", record.args),
            "open_app": ("open_app", record.args),
            "delete_folder": ("delete", {"path": record.args.get("path")}),
            "move": ("move", record.args),
            "restore_from_trash": (
                "move",
                {"src": record.args.get("src"), "dst": record.args.get("dst")},
            ),
            "clipboard_set": ("clipboard_set", record.args),
            "set_volume": ("set_volume", record.args),
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
                UndoRecord(action_id=record.action_id, operation="delete_folder", args=record.args)
            )
        online = c.hub.online()
        if not online:
            return False
        result = await c.hub.invoke(
            record.device_id or online[0].id,
            DeviceAction(
                action="write_file",
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
        "close_app",
        "open_app",
        "delete_folder",
        "move",
        "restore_from_trash",
        "clipboard_set",
        "set_volume",
    ):
        c.undo.register_executor(operation, device_undo)
    c.undo.register_executor("restore_file", restore_file)
    c.undo.register_executor("memory_forget", memory_forget)
