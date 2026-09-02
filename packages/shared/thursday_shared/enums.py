"""Enumerations shared across every layer."""

from __future__ import annotations

from enum import IntEnum, StrEnum


class TaskState(StrEnum):
    """PART 5. Terminal states are COMPLETED, FAILED, CANCELLED.

    Four states describe *not running*, and the difference between them is the reason:

    * ``WAITING`` — blocked on a dependency inside the plan
    * ``WAITING_APPROVAL`` — blocked on a human decision
    * ``BLOCKED`` — blocked on something outside the plan (a device is offline)
    * ``PAUSED`` — the owner stopped it deliberately

    Collapsing them would lose the one thing the owner needs to know: what to do about it.
    """

    NEW = "NEW"
    PLANNING = "PLANNING"
    #: Planned and authorised, not yet picked up by a worker. This is what a queue schedules.
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    BLOCKED = "BLOCKED"
    PAUSED = "PAUSED"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_STATES


_TERMINAL_STATES = frozenset({TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED})

#: Legal transitions. Anything not listed here raises rather than corrupting state.
TASK_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.NEW: frozenset({TaskState.PLANNING, TaskState.CANCELLED, TaskState.FAILED}),
    TaskState.PLANNING: frozenset(
        {
            TaskState.READY,
            TaskState.RUNNING,
            TaskState.WAITING_APPROVAL,
            TaskState.BLOCKED,
            TaskState.CANCELLED,
            TaskState.FAILED,
        }
    ),
    TaskState.READY: frozenset(
        {
            TaskState.RUNNING,
            TaskState.WAITING_APPROVAL,
            TaskState.BLOCKED,
            TaskState.PAUSED,
            TaskState.CANCELLED,
            TaskState.FAILED,
        }
    ),
    TaskState.RUNNING: frozenset(
        {
            TaskState.WAITING,
            TaskState.WAITING_APPROVAL,
            TaskState.BLOCKED,
            TaskState.PAUSED,
            TaskState.VERIFYING,
            TaskState.CANCELLED,
            TaskState.FAILED,
        }
    ),
    TaskState.WAITING: frozenset(
        {TaskState.RUNNING, TaskState.PAUSED, TaskState.CANCELLED, TaskState.FAILED}
    ),
    TaskState.WAITING_APPROVAL: frozenset(
        {
            TaskState.RUNNING,
            TaskState.READY,
            TaskState.BLOCKED,
            TaskState.CANCELLED,
            TaskState.FAILED,
        }
    ),
    TaskState.BLOCKED: frozenset(
        {TaskState.READY, TaskState.RUNNING, TaskState.CANCELLED, TaskState.FAILED}
    ),
    # A paused task resumes where it stopped; it does not restart.
    TaskState.PAUSED: frozenset(
        {TaskState.RUNNING, TaskState.READY, TaskState.CANCELLED, TaskState.FAILED}
    ),
    # VERIFYING may return to RUNNING: a failed verification means more work, not success.
    TaskState.VERIFYING: frozenset(
        {TaskState.COMPLETED, TaskState.RUNNING, TaskState.FAILED, TaskState.CANCELLED}
    ),
    TaskState.COMPLETED: frozenset(),
    TaskState.FAILED: frozenset(),
    TaskState.CANCELLED: frozenset(),
}


class PermissionLevel(IntEnum):
    """§36. Ordered — comparisons are meaningful."""

    READ = 0
    OPEN = 1
    MODIFY = 2
    EXTERNAL = 3
    SYSTEM = 4
    ADMIN = 5


class PolicyDecision(StrEnum):
    """PART 20. Four policies, because "ask" has two very different meanings.

    ``ASK_ONCE`` may produce a scoped, expiring grant when the owner chooses "always allow".
    ``ASK_ALWAYS`` may never produce a grant under any scope — deleting, sending, purchasing
    and elevating are asked every time, forever, so that no sequence of hurried approvals
    can quietly turn them into standing permissions.
    """

    AUTO = "AUTO"
    ASK_ONCE = "ASK_ONCE"
    ASK_ALWAYS = "ASK_ALWAYS"
    BLOCK = "BLOCK"

    @property
    def requires_approval(self) -> bool:
        return self in (PolicyDecision.ASK_ONCE, PolicyDecision.ASK_ALWAYS)

    @property
    def grantable(self) -> bool:
        """Whether an approval for this may be remembered as a standing grant."""
        return self is PolicyDecision.ASK_ONCE


class RiskLevel(StrEnum):
    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class DataSensitivity(IntEnum):
    """§34. SECRET must never reach a cloud provider."""

    PUBLIC = 0
    INTERNAL = 1
    PRIVATE = 2
    HIGHLY_PRIVATE = 3
    SECRET = 4


class ModelTier(StrEnum):
    """§33."""

    FAST = "FAST"
    STANDARD = "STANDARD"
    REASONING = "REASONING"
    VISION = "VISION"
    LOCAL = "LOCAL"


class VoiceMode(StrEnum):
    """§6."""

    NORMAL = "NORMAL"
    THINKING = "THINKING"
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    URGENT = "URGENT"
    QUIET = "QUIET"


class AvatarState(StrEnum):
    """§63."""

    IDLE = "IDLE"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    WORKING = "WORKING"
    SPEAKING = "SPEAKING"
    WARNING = "WARNING"


class MemoryLayer(StrEnum):
    """§7."""

    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PREFERENCE = "preference"
    PROCEDURAL = "procedural"
    PROJECT = "project"
    KNOWLEDGE = "knowledge"


class MemorySource(StrEnum):
    """§74 provenance."""

    USER = "user"
    FILE = "file"
    EMAIL = "email"
    DATABASE = "database"
    WEB = "web"
    AGENT = "agent"
    CAMERA = "camera"
    SENSOR = "sensor"
    INFERENCE = "inference"


#: Source trust ranking, used when deciding whether new information may supersede old (§11).
SOURCE_RANK: dict[MemorySource, int] = {
    MemorySource.USER: 100,
    MemorySource.FILE: 80,
    MemorySource.DATABASE: 78,
    MemorySource.EMAIL: 70,
    MemorySource.CAMERA: 55,
    MemorySource.SENSOR: 55,
    MemorySource.WEB: 50,
    MemorySource.AGENT: 40,
    MemorySource.INFERENCE: 30,
}


class MemoryDecision(StrEnum):
    """PART 39. Four outcomes, because "should I remember this?" has four honest answers.

    ``ASK_USER`` is the one that matters: a memory Thursday is unsure about should be
    confirmed rather than guessed at, because a wrong long-term memory is worse than none.
    """

    STORE = "STORE"
    TEMPORARY = "TEMPORARY"
    IGNORE = "IGNORE"
    ASK_USER = "ASK_USER"


class MemoryRelation(StrEnum):
    """PART 41. How one memory relates to another. Never a silent overwrite."""

    SUPERSEDES = "supersedes"
    CONTRADICTS = "contradicts"
    UPDATES = "updates"
    DERIVED_FROM = "derived_from"


class IntentKind(StrEnum):
    """PART 9. Each category maps to the capabilities the Agent Router needs."""

    ANSWER = "ANSWER"
    SEARCH = "SEARCH"
    COMPUTER_ACTION = "COMPUTER_ACTION"
    BROWSER_ACTION = "BROWSER_ACTION"
    FILE_ACTION = "FILE_ACTION"
    DATA_ANALYSIS = "DATA_ANALYSIS"
    DOCUMENT = "DOCUMENT"
    COMMUNICATION = "COMMUNICATION"
    CALENDAR = "CALENDAR"
    DESIGN = "DESIGN"
    VISION = "VISION"
    AUTOMATION = "AUTOMATION"
    DEVICE_CONTROL = "DEVICE_CONTROL"
    MULTI_STEP_TASK = "MULTI_STEP_TASK"
    UNKNOWN = "UNKNOWN"

    # Control intents: they never reach the planner, so they are kept out of the
    # capability-bearing list above.
    MEMORY_WRITE = "MEMORY_WRITE"
    MEMORY_RECALL = "MEMORY_RECALL"
    #: "forget X" and "don't remember this". Explicit memory commands outrank the write
    #: policy: the owner asking is the strongest signal there is (§7).
    MEMORY_FORGET = "MEMORY_FORGET"
    STATUS = "STATUS"
    STOP = "STOP"
    APPROVE = "APPROVE"
    CLARIFY = "CLARIFY"


#: Which agent capabilities each intent implies (PART 9's ``required_capabilities``).
INTENT_CAPABILITIES: dict[IntentKind, tuple[str, ...]] = {
    IntentKind.SEARCH: ("research", "search"),
    IntentKind.COMPUTER_ACTION: ("app_control", "os"),
    IntentKind.BROWSER_ACTION: ("browser", "web"),
    IntentKind.FILE_ACTION: ("file",),
    IntentKind.DATA_ANALYSIS: ("data", "analysis"),
    IntentKind.DOCUMENT: ("document", "writing"),
    IntentKind.COMMUNICATION: ("communication",),
    IntentKind.CALENDAR: ("calendar", "scheduling"),
    IntentKind.DESIGN: ("design",),
    IntentKind.VISION: ("vision", "screen"),
    IntentKind.AUTOMATION: ("automation",),
    IntentKind.DEVICE_CONTROL: ("app_control", "diagnostics"),
    IntentKind.MULTI_STEP_TASK: (),
    IntentKind.MEMORY_RECALL: ("recall",),
}


class AutonomyLevel(IntEnum):
    """PART 97. How much Thursday may *do* unasked — distinct from proactivity, which
    governs how much it may *say* unasked.

    Raising this can only relax ``ASK_ONCE`` actions. ``ASK_ALWAYS`` and ``BLOCK`` are
    unaffected at every level, including the highest: the most permissive setting is still
    not admin.
    """

    SUGGEST_ONLY = 0
    SAFE_ACTIONS = 1
    MODERATE = 2
    HIGH = 3


class AgentVerdict(StrEnum):
    """§18."""

    PASS = "PASS"  # noqa: S105 - a verification verdict, not a credential
    RETRY = "RETRY"
    ESCALATE = "ESCALATE"


class DeviceStatus(StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"
    SLEEPING = "sleeping"


class ControlTier(IntEnum):
    """§19. Lower is preferred; GUI clicking is the last resort."""

    API = 1
    APP_INTEGRATION = 2
    BROWSER = 3
    OS_API = 4
    GUI = 5


class ProactivityLevel(IntEnum):
    """§46."""

    OFF = 0
    LOW = 1
    NORMAL = 2
    HIGH = 3


class NotificationPriority(StrEnum):
    """§67."""

    CRITICAL = "CRITICAL"
    IMPORTANT = "IMPORTANT"
    NORMAL = "NORMAL"
    LOW = "LOW"


class ApprovalState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ApprovalScope(StrEnum):
    ONCE = "once"
    SESSION = "session"
    ALWAYS = "always"


class StepKind(StrEnum):
    TOOL = "tool"
    AGENT = "agent"
    DEVICE = "device"
    ASK_USER = "ask_user"
