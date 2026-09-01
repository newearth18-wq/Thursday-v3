"""Enumerations shared across every layer."""

from __future__ import annotations

from enum import IntEnum, StrEnum


class TaskState(StrEnum):
    """§42. Terminal states are COMPLETED, FAILED, CANCELLED."""

    NEW = "NEW"
    PLANNING = "PLANNING"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    BLOCKED = "BLOCKED"
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
        {TaskState.RUNNING, TaskState.WAITING_APPROVAL, TaskState.BLOCKED,
         TaskState.CANCELLED, TaskState.FAILED}
    ),
    TaskState.RUNNING: frozenset(
        {TaskState.WAITING, TaskState.WAITING_APPROVAL, TaskState.BLOCKED,
         TaskState.VERIFYING, TaskState.CANCELLED, TaskState.FAILED}
    ),
    TaskState.WAITING: frozenset({TaskState.RUNNING, TaskState.CANCELLED, TaskState.FAILED}),
    TaskState.WAITING_APPROVAL: frozenset(
        {TaskState.RUNNING, TaskState.BLOCKED, TaskState.CANCELLED, TaskState.FAILED}
    ),
    TaskState.BLOCKED: frozenset({TaskState.RUNNING, TaskState.CANCELLED, TaskState.FAILED}),
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
    """§37."""

    AUTO = "AUTO"
    ASK = "ASK"
    BLOCK = "BLOCK"


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


class IntentKind(StrEnum):
    CHAT = "CHAT"
    ANSWER = "ANSWER"
    SEARCH = "SEARCH"
    DEVICE_ACTION = "DEVICE_ACTION"
    FILE_OP = "FILE_OP"
    ANALYZE = "ANALYZE"
    CREATE = "CREATE"
    AUTOMATE = "AUTOMATE"
    RECALL = "RECALL"
    STATUS = "STATUS"
    STOP = "STOP"
    APPROVE = "APPROVE"
    CLARIFY = "CLARIFY"


class AgentVerdict(StrEnum):
    """§18."""

    PASS = "PASS"
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
