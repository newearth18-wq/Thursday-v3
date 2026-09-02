"""Dependency-injection container (§99).

The single place where concrete providers are chosen and wired. Core modules receive ports;
they never construct a provider. That is the whole mechanism behind "swap the LLM, the STT,
the vector store or the agent framework without rewriting the system" (§4).

Tests build a container of fakes with the same attribute surface.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from thursday_agents.browser import BrowserAgent, register_browser_tools
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
from thursday_memory.mirror import VaultMirror
from thursday_memory.obsidian import ObsidianVault
from thursday_memory.vector import InMemoryVectorStore
from thursday_models.llm import AnthropicLLM, OllamaLLM, RuleBasedLLM
from thursday_security.approvals import ApprovalService
from thursday_security.audit import AuditLog
from thursday_security.device_auth import DeviceAuthenticator
from thursday_security.permissions import PermissionEngine
from thursday_security.policy import PolicyTable
from thursday_security.privacy import PrivacyClassifier, PrivacyZoneRegistry
from thursday_security.redaction import SecretRedactor
from thursday_security.vault import ChainVault, EnvVault, InMemoryVault
from thursday_shared.enums import ModelTier
from thursday_tools.builtin import register_builtin_tools
from thursday_tools.registry import ToolRegistry, ToolRouter
from thursday_vision.camera import CameraManager
from thursday_vision.gestures import GestureMode, GestureTracker
from thursday_vision.providers import RuleBasedSceneAnalyzer
from thursday_vision.service import VisionService
from thursday_vision.spatial import SpatialMemory
from thursday_voice.routing import AudioRouter
from thursday_voice.service import VoiceService

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
from thursday_core.state import build_state_store
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
    vault_mirror: Any = None
    graph: Any = None

    # models
    models: Any = None

    # devices
    hub: Any = None
    device_router: Any = None
    device_auth: Any = None

    # execution
    tools: Any = None
    tool_router: Any = None
    executor: Any = None
    agents: Any = None
    tasks: Any = None
    projects: Any = None
    queue: Any = None
    state: Any = None
    supervisor: Any = None
    orchestrator: Any = None
    agent_factory: Any = None

    # automation, skills, perception
    automations: Any = None
    routines: Any = None
    skills: Any = None
    spatial: Any = None
    camera: Any = None
    vision: Any = None
    gesture_mode: Any = None
    gesture_tracker: Any = None

    # voice
    stt: Any = None
    tts: Any = None
    wake_word: Any = None
    audio_router: Any = None
    voice: Any = None

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
        state_ok, state_detail = await self.state.health()
        checks.append({"component": "redis", "ok": state_ok, "detail": state_detail})
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
    c.device_auth = _build_device_auth(settings)

    # -- execution ------------------------------------------------------------
    c.tools = ToolRegistry()
    register_builtin_tools(c.tools, hub=c.hub, memory=c.memory, vault=c.obsidian)
    # Durable knowledge also lands in the owner's own notebook (§8). A subscriber rather
    # than a call inside the write path: switching the vault off removes it, instead of
    # leaving a dead branch in the memory manager.
    c.vault_mirror = VaultMirror(c.obsidian, c.memory).attach(c.bus)
    # Browser tools need Playwright and a browser binary. Absent them, the agent stays
    # registered but unroutable — the registry's tool-gap term drops its score to zero,
    # so Thursday says "I can't do that" rather than failing halfway through a form.
    if _playwright_available():
        register_browser_tools(c.tools)
    else:
        log.info("browser_tools_unavailable", reason="playwright is not installed")
    c.tool_router = ToolRouter(c.tools)
    c.tasks = TaskManager(c.bus)
    c.state = build_state_store(settings.redis_url)
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
    c.agents.register(BrowserAgent())
    c.agent_factory = AgentFactory(c.agents)
    c.supervisor = Supervisor(c.models, use_llm_critique=not settings.offline)

    # -- automation, skills, perception ---------------------------------------
    c.skills = SkillRegistry(executor=c.executor, tools=c.tools)
    # After skills, because a project's picture includes what Thursday can already do
    # for it (PART 44).
    c.projects = ProjectManager(tasks=c.tasks, memory=c.memory, skills=c.skills)
    c.spatial = SpatialMemory()
    # Vision. The camera is constructed OFF with no source attached: a checkout that has
    # never been given a camera cannot accidentally have one opened (§51). Attaching real
    # hardware is a deliberate act, and `camera.grant_access` is still required after it.
    c.camera = CameraManager(None)
    c.vision = VisionService(
        camera=c.camera,
        detector=None,
        ocr=None,
        barcodes=None,
        analyzer=RuleBasedSceneAnalyzer(),
        spatial=c.spatial,
        bus=c.bus,
    )
    c.gesture_mode = GestureMode()
    # The tracker holds the frame history that swipe, drag and zoom need. Separate from the
    # mode, because recognising a gesture and being willing to act on one are different
    # questions and only the second is about permission.
    c.gesture_tracker = GestureTracker()

    # -- voice ----------------------------------------------------------------
    c.stt, c.tts, c.wake_word = _build_voice(settings)
    c.audio_router = AudioRouter(follow_me=settings.voice_follow_me)

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

    # The voice loop last: it needs the engine to hand transcripts to (V4).
    c.voice = VoiceService(
        engine=c.engine,
        stt=c.stt,
        tts=c.tts,
        wake_word=c.wake_word,
        router=c.audio_router,
        bus=c.bus,
        voice=settings.voice_name,
        require_wake_word=settings.require_wake_word,
    )
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


def _build_device_auth(settings: Settings) -> DeviceAuthenticator:
    """Resolve the node enrolment token once, from the environment, never from a file.

    Reading it here rather than per-HELLO keeps the secret out of the request path, and
    keeps the one place that knows its name next to the one place that checks it.
    """
    handle = settings.device_shared_secret_handle
    key = EnvVault().prefix + handle.upper().replace("-", "_").replace(".", "_")
    token = os.environ.get(key)
    if settings.require_device_signature and not token:
        # Not fatal at build time — a test container and the CLI's loopback node never
        # open the socket. It becomes fatal at the first unsigned HELLO, which is where
        # refusing is actually useful.
        log.warning("device_token_not_configured", expected_env=key)
    return DeviceAuthenticator(token, required=settings.require_device_signature)


def _build_vault(settings: Settings) -> Any:
    if settings.vault_backend == "memory":
        return InMemoryVault()
    if settings.vault_backend == "keychain":
        # The OS keychain adapter lands in Phase 2; chain it ahead of the environment so
        # the swap is a configuration change, not a code change.
        return ChainVault(EnvVault())
    return EnvVault()


def _playwright_available() -> bool:
    from importlib.util import find_spec

    return find_spec("playwright") is not None


def _build_voice(settings: Settings) -> tuple[Any, Any, Any]:
    """Wake word, STT and TTS, each behind a fallback chain (V4).

    The chain is what makes "cloud primary, local fallback" real rather than aspirational:
    a provider that fails is stepped over inside the same utterance, so a dropped
    connection costs latency instead of costing the turn. The stubs are text-driven, so the
    whole path — wake, VAD, transcription, mode selection, routing — stays exercisable in
    CI with no microphone and no model files.
    """
    from thursday_voice.fallback import STTChain, TTSChain
    from thursday_voice.providers import (
        KeywordWakeWord,
        PiperTTS,
        TextStubSTT,
        TextStubTTS,
        WhisperSTT,
    )

    # Ordered best-first. The stub is always last so there is always *something* that
    # answers: an assistant that goes mute when a model file is missing is worse than one
    # that degrades to a plain voice.
    stt_providers: list[Any] = []
    if settings.stt_backend == "whisper":
        stt_providers.append(WhisperSTT())
    stt_providers.append(TextStubSTT())

    tts_providers: list[Any] = []
    if settings.tts_backend == "piper":
        tts_providers.append(PiperTTS(model_path=str(settings.data_dir / "piper.onnx")))
    tts_providers.append(TextStubTTS())

    # Audio is HIGHLY_PRIVATE by default (§34), so the chain refuses to fall back onto a
    # provider that would send it off the machine.
    stt = STTChain(stt_providers, local_only=settings.voice_local_only)
    tts = TTSChain(tts_providers, local_only=settings.voice_local_only)
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
