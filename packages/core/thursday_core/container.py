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

from thursday_agents.automation import AutomationAgent
from thursday_agents.browser import BrowserAgent, register_browser_tools
from thursday_agents.calendar import CalendarAgent
from thursday_agents.coding import CodingAgent
from thursday_agents.communication import CommunicationAgent
from thursday_agents.computer import ComputerAgent
from thursday_agents.data import DataAgent
from thursday_agents.design import DesignAgent
from thursday_agents.document import DocumentAgent
from thursday_agents.factory import AgentFactory
from thursday_agents.files import FileAgent
from thursday_agents.media import MediaAgent
from thursday_agents.ports import LocalCalendar, LocalOutbox
from thursday_agents.registry import AgentRegistry
from thursday_agents.research import ResearchAgent
from thursday_agents.vision import VisionAgent
from thursday_automation.engine import AutomationEngine, ProactivityGate
from thursday_automation.offers import OfferBook
from thursday_automation.proactive import ProactiveEngine
from thursday_automation.routines import RoutineLearner
from thursday_automation.skills.learning import SkillObserver
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
from thursday_security.credentials import FileCredentialStore
from thursday_security.device_auth import DeviceAuthenticator
from thursday_security.pairing import PairingService
from thursday_security.permissions import PermissionEngine
from thursday_security.policy import PolicyTable
from thursday_security.privacy import PrivacyClassifier, PrivacyZoneRegistry
from thursday_security.redaction import SecretRedactor
from thursday_security.remote import RemoteCommandGate
from thursday_security.vault import ChainVault, EnvVault, InMemoryVault, KeychainVault
from thursday_shared.enums import ModelTier
from thursday_shared.errors import ConfigurationError
from thursday_tools.builtin import register_builtin_tools
from thursday_tools.registry import ToolRegistry, ToolRouter
from thursday_vision.camera import CameraManager
from thursday_vision.gestures import GestureMode, GestureTracker
from thursday_vision.providers import RuleBasedSceneAnalyzer
from thursday_vision.service import VisionService
from thursday_vision.spatial import SpatialMemory
from thursday_voice.routing import AudioRouter
from thursday_voice.service import VoiceService

from thursday_core.backup import BackupService, default_components
from thursday_core.briefing import Briefer, DecisionJournal
from thursday_core.bus import InProcessEventBus
from thursday_core.composer import ResponseComposer
from thursday_core.config import Settings, get_settings
from thursday_core.context import ContextEngine
from thursday_core.cost import CostMeter
from thursday_core.device_router import DeviceRouter
from thursday_core.execution import ToolExecutor
from thursday_core.focus import DeviceFocus
from thursday_core.goals import GoalManager, PriorityQueue
from thursday_core.logging import configure_logging, get_logger
from thursday_core.metrics import MetricsCollector, build_registry
from thursday_core.model_router import ModelRouter
from thursday_core.orchestrator import AgentOrchestrator
from thursday_core.persistence import NullRepository, SqlRepository
from thursday_core.planner import Planner
from thursday_core.projects import ProjectManager
from thursday_core.reasoning import ReasoningEngine
from thursday_core.recovery import SelfRecovery
from thursday_core.reflection import FeedbackLog, SelfEvaluator
from thursday_core.resumption import interrupted
from thursday_core.state import build_state_store
from thursday_core.supervisor import Supervisor
from thursday_core.tasks import TaskManager, TaskQueue
from thursday_core.undo import UndoRegistry
from thursday_core.updates import (
    LocalReleaseSource,
    PinnedHttpReleaseSource,
    UpdateService,
)
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
    costs: Any = None
    backups: Any = None
    updates: Any = None
    #: Whether state actually outlives this process (Sprint 51). False is a supported
    #: configuration and not a degraded one — but it must never be a silent assumption.
    persistent: bool = False
    metrics: Any = None
    #: Whether state actually outlives this process (Sprint 51). False is a supported
    #: configuration and not a degraded one — but it must never be a silent assumption.
    persistent: bool = False

    # devices
    hub: Any = None
    device_router: Any = None
    device_focus: Any = None
    remote_gate: Any = None
    device_auth: Any = None
    pairing: Any = None

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
    calendar: Any = None
    outbox: Any = None
    skill_observer: Any = None
    offers: Any = None
    proactive: Any = None
    goals: Any = None
    priorities: Any = None
    journal: Any = None
    briefer: Any = None
    evaluator: Any = None
    feedback: Any = None
    recovery: Any = None
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
                "detail": (
                    f"{_mask(self.settings.resolved_database_url)} — "
                    + ("state is durable" if self.persistent else "state lives for this process")
                ),
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
        audit = self.audit.health()
        checks.append(
            {
                "component": "audit",
                # Degraded counts as unhealthy, not as a note. What a failed write lost cannot
                # be recovered and the chain cannot detect it — a missing entry leaves a chain
                # that verifies — so if this is not red, nothing says so at all.
                "ok": audit["chain_intact"] and not audit["degraded"],
                "detail": (
                    f"{audit['entries']} entries, hash chain "
                    + ("intact" if audit["chain_intact"] else "BROKEN")
                    # The previous version said "hash chain intact" unconditionally, so a
                    # broken chain reported ok=False next to the words "chain intact".
                    + (
                        f", {audit['lost']} entries could not be stored"
                        if audit["degraded"]
                        else ""
                    )
                ),
            }
        )
        spend = self.costs.health()
        checks.append(
            {
                "component": "spend",
                # A lost charge is not a lost record: it means the ceiling *under-binds* after
                # the next restart, so the owner spends more than they set out to. A cap
                # nobody can trust is a cap that is not doing its job.
                "ok": not spend["degraded"],
                "detail": (
                    f"${spend['today_usd']:.2f} today over {spend['charges']} calls"
                    + (
                        f", {spend['lost']} charges could not be stored"
                        if spend["degraded"]
                        else ""
                    )
                ),
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
    c.audit = AuditLog(c.redactor, repository=_audit_repository(settings, c))
    c.vault = _build_vault(settings)

    # -- security -------------------------------------------------------------
    c.policy = PolicyTable()
    c.zones = PrivacyZoneRegistry()
    c.classifier = PrivacyClassifier(c.redactor)
    c.metrics = build_registry()
    c.permissions = PermissionEngine(policy=c.policy, zones=c.zones, metrics=c.metrics)
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
        repository=_memory_repository(settings, c),
    )
    c.obsidian = ObsidianVault(
        settings.obsidian_vault, redactor=c.redactor, enabled=settings.obsidian_enabled
    )
    c.obsidian.ensure_structure()
    c.graph = KnowledgeGraph()

    # -- models ---------------------------------------------------------------
    c.costs = CostMeter(
        daily_usd=settings.daily_cost_cap_usd,
        monthly_usd=settings.monthly_cost_cap_usd,
        repository=_spend_repository(settings, c),
    )
    c.models = _build_models(settings, c.vault, c.costs, c.redactor, c.metrics)

    # -- devices --------------------------------------------------------------
    c.remote_gate = RemoteCommandGate()
    c.hub = DeviceHub(c.bus, remote_gate=c.remote_gate)
    c.device_router = DeviceRouter(c.hub)
    c.device_focus = DeviceFocus()
    # Pairings outlive the process. An in-memory registry would mean a restart locks out
    # every paired node — it signs with its key, the core no longer knows the key, and the
    # node correctly refuses to fall back to the shared token (ADR 0029).
    c.pairing = PairingService(
        store=FileCredentialStore(settings.data_dir / "device_credentials.json")
    )
    c.device_auth = _build_device_auth(settings, c.pairing)

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
    c.tasks = TaskManager(c.bus, repository=_task_repository(settings, c))
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
    c.agents.register(DataAgent())
    c.agents.register(DocumentAgent())
    c.agents.register(FileAgent())
    c.agents.register(CodingAgent())
    c.agents.register(DesignAgent())
    c.agents.register(MediaAgent())
    c.agent_factory = AgentFactory(c.agents)
    c.supervisor = Supervisor(c.models, use_llm_critique=not settings.offline)

    # -- automation, skills, perception ---------------------------------------
    c.skills = SkillRegistry(executor=c.executor, tools=c.tools, agents=c.agents)
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
        device_focus=c.device_focus,
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
    c.skill_observer = SkillObserver()
    c.skill_observer.attach(c.bus)

    # Noticing and offering are separate objects on purpose: "what did Thursday notice" and
    # "what did Thursday do about it" stay separately inspectable and separately testable.
    c.offers = OfferBook()
    c.proactive = ProactiveEngine(gate=c.automations.gate)

    c.goals = GoalManager()
    c.evaluator = SelfEvaluator()
    c.feedback = FeedbackLog()
    c.recovery = SelfRecovery()
    # Only repairs that restore a capability Thursday already had. `register` refuses
    # anything on the never-automatic list, so a forbidden repair cannot be wired in
    # here by accident and found later.
    c.recovery.register("reconnect_node", lambda: None)
    c.recovery.register("switch_model", lambda: None)
    c.priorities = PriorityQueue(c.tasks, c.goals)
    c.journal = DecisionJournal()
    c.briefer = Briefer(
        tasks=c.tasks,
        approvals=c.approvals,
        calendar=c.calendar,
        health=c.health,
        offers=c.offers,
        journal=c.journal,
        memory=c.memory,
        skills=c.skill_observer,
        costs=c.costs,
    )

    # Built last: it needs every service it backs up to exist first (Sprint 47).
    c.backups = BackupService(default_components(c), redactor=c.redactor)

    c.updates = _build_updates(settings, c.backups)

    # Metrics last: the gauges read from services above, and the collector subscribes to the
    # bus rather than each of them having to remember to report (Sprint 49).
    MetricsCollector(c.metrics).attach(c.bus)
    c.metrics.register_gauge_source(
        "thursday_devices_online",
        help="Devices currently connected.",
        read=lambda: len(c.hub.online()),
    )
    c.metrics.register_gauge_source(
        "thursday_spend_today_usd",
        help="What has been spent today, against the daily cap.",
        read=lambda: c.costs.spent_today(),
    )
    c.metrics.register_gauge_source(
        "thursday_approvals_pending",
        help="Approvals waiting on the owner. A number that only goes up is a stuck system.",
        read=lambda: len(c.approvals.pending()),
    )

    # Registered here rather than beside the others because they hold references to
    # services built further down the file. An agent that needs a collaborator takes it in
    # its constructor rather than reaching for the container: the collaborator is then
    # visible in the wiring, and a test can supply a different one.
    # No account is configured, so the local adapters stand in — real behaviour, nothing
    # leaving the machine (ADR 0001). What they are *not* is the owner's actual calendar or
    # mail, and docs/21 says so rather than letting the agent names imply otherwise.
    c.calendar = LocalCalendar()
    c.outbox = LocalOutbox()
    c.agents.register(CalendarAgent(c.calendar))
    c.agents.register(CommunicationAgent(c.outbox))
    c.agents.register(VisionAgent(c.vision))
    c.agents.register(AutomationAgent(c.automations, routines=c.routines, skills=c.skill_observer))

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


def _build_device_auth(settings: Settings, pairing: Any = None) -> DeviceAuthenticator:
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
    return DeviceAuthenticator(token, required=settings.require_device_signature, pairing=pairing)


def _build_vault(settings: Settings) -> Any:
    if settings.vault_backend == "memory":
        return InMemoryVault()
    if settings.vault_backend == "keychain":
        keychain = KeychainVault()
        if not keychain.available:
            # Fail closed, exactly as `DeviceAuthenticator` does for a missing device token.
            # This branch used to return `ChainVault(EnvVault())` and say nothing, so a
            # deployment that configured `keychain` got the environment vault and believed
            # its secrets were in the OS keychain. An imagined protection is worse than a
            # known weakness: the known one gets compensated for.
            raise ConfigurationError(
                "vault_backend is 'keychain' and this machine has no keychain Thursday can "
                "use. Install one (GNOME Keyring or KWallet on Linux), or set "
                "vault_backend='env' to store secrets in the environment deliberately."
            )
        # Chained ahead of the environment so secrets not yet moved are still readable —
        # migration, not a fallback for a missing keychain.
        return ChainVault(keychain, EnvVault())
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


def _build_models(
    settings: Settings,
    vault: Any,
    meter: CostMeter | None = None,
    redactor: Any = None,
    metrics: Any = None,
) -> ModelRouter:
    router = ModelRouter(
        allow_cloud=settings.allow_cloud, meter=meter, redactor=redactor, metrics=metrics
    )
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


def _build_updates(settings: Settings, backups: Any) -> UpdateService:
    """The update channel, from configuration and from nowhere else (§120).

    No installer is wired here. This build can *tell the owner* an update exists and can
    verify one, and it deliberately cannot swap its own code over: replacing the running
    system is a platform concern, and a half-built installer is worse than none.
    """
    from thursday_shared import __version__

    source: Any = None
    if settings.update_manifest_path is not None:
        source = LocalReleaseSource(
            path=settings.update_manifest_path, base_url=settings.update_channel_url
        )
    elif settings.update_channel_url:
        source = PinnedHttpReleaseSource(base_url=settings.update_channel_url)

    return UpdateService(
        current_version=__version__,
        source=source,
        signing_key=settings.update_signing_key,
        backups=backups,
    )


def _task_repository(settings: Settings, container: Container) -> Any:
    """Where tasks live between runs (ADR 0039)."""
    if not settings.persist_tasks:
        return NullRepository()

    from thursday_shared.db.models import Task as TaskRow
    from thursday_shared.db.session import init_engine, session_scope
    from thursday_shared.models import Task as TaskModel

    init_engine(settings)
    container.persistent = True
    return SqlRepository(
        TaskRow,
        session_scope=session_scope,
        defaults={"user_id": settings.owner_id},
        order_by="created_at",
        fields=set(TaskModel.model_fields),
    )


def _spend_repository(settings: Settings, container: Container) -> Any:
    """Where the spend ledger is kept between runs (§61).

    None rather than `NullRepository` when persistence is off, so `CostMeter` skips the
    storage path entirely instead of awaiting a no-op on every model call.
    """
    if not settings.persist_costs:
        return None

    from thursday_shared.db.models import ModelSpend
    from thursday_shared.db.session import init_engine, session_scope

    init_engine(settings)
    container.persistent = True
    return SqlRepository(
        ModelSpend,
        session_scope=session_scope,
        defaults={"user_id": settings.owner_id},
        order_by="at",
        fields={
            "id",
            "at",
            "provider",
            "tier",
            "tokens_in",
            "tokens_out",
            "usd",
            "task_id",
            "agent",
        },
    )


def _audit_repository(settings: Settings, container: Container) -> Any:
    """Where the audit trail is kept between runs.

    Ordered by `ts` on load, because `verify_chain` walks entries comparing each `prev_hash`
    to the one before: rows in arbitrary order would fail a chain that is perfectly intact.
    """
    if not settings.persist_audit:
        return NullRepository()

    from thursday_security.audit import AuditEntry
    from thursday_shared.db.models import AuditLogRow
    from thursday_shared.db.session import init_engine, session_scope

    init_engine(settings)
    container.persistent = True
    return SqlRepository(
        AuditLogRow,
        session_scope=session_scope,
        defaults={"user_id": settings.owner_id},
        order_by="ts",
        fields=set(AuditEntry.model_fields),
    )


def _memory_repository(settings: Settings, container: Container) -> Any:
    """Where memories are kept between runs (Sprint 51).

    `NullRepository` unless a database is configured, and that is a real configuration rather
    than a fallback: the whole test suite and `python -m apps.cli` run on it. What it must not
    do is claim durability it does not have, which is why it also sets `container.persistent`.
    """
    if not settings.persist_memory:
        return NullRepository()

    from thursday_shared.db.models import Memory
    from thursday_shared.db.session import init_engine, session_scope
    from thursday_shared.models import MemoryRecord

    init_engine(settings)
    container.persistent = True
    return SqlRepository(
        Memory,
        session_scope=session_scope,
        # Single-tenant: the column exists because the schema was drawn for a world where
        # Thursday might not be. Deriving it rather than storing a second copy of the truth.
        defaults={"user_id": settings.owner_id},
        order_by="created_at",
        fields=set(MemoryRecord.model_fields),
    )


async def start(container: Container) -> Container:
    """Bring a built container up: load what was kept, then report what is real.

    Separate from `build_container` because loading is async and construction is not, and
    because a container that reaches for a database while being assembled cannot be built in
    a test. Callers that skip this get exactly the behaviour they had before persistence
    existed, which is why every existing test still passes without calling it.
    """
    memories = await container.memory.restore()
    entries = await container.audit.restore()
    charges = await container.costs.restore()
    tasks = await container.tasks.restore()
    log.info(
        "thursday_state_loaded",
        persistent=container.persistent,
        memories=memories,
        audit_entries=entries,
        audit_chain_intact=container.audit.verify_chain(),
        spend_charges=charges,
        tasks=tasks,
        interrupted=len(interrupted(container.tasks)),
    )
    return container
