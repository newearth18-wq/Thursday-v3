"""Thursday's error taxonomy.

Errors are typed because the recovery ladder (§60) branches on *why* something failed:
a permission denial is not retried, a timeout may be, a blocked action never is.
"""

from __future__ import annotations

from typing import Any


class ThursdayError(Exception):
    """Base class. ``retryable`` drives the failure-recovery ladder."""

    code = "thursday_error"
    retryable = False

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


class ConfigurationError(ThursdayError):
    code = "configuration_error"


class PermissionDenied(ThursdayError):
    """The Permission Engine returned BLOCK, or an approval was rejected/expired."""

    code = "permission_denied"


class ApprovalRequired(ThursdayError):
    """Execution paused pending a human decision (§38)."""

    code = "approval_required"


class DeviceUnavailable(ThursdayError):
    code = "device_unavailable"
    retryable = True


class DeviceActionFailed(ThursdayError):
    code = "device_action_failed"
    retryable = True


class DeviceActionRefused(ThursdayError):
    """A cross-device instruction the remote gate would not relay (§9.4, V8).

    Not retryable and deliberately distinct from `DeviceActionFailed`: nothing was
    attempted on the target machine, so "it failed" would be the wrong thing to tell the
    owner and retrying would be the wrong thing to do. The remedy is trust or an approval,
    neither of which a second attempt supplies.
    """

    code = "device_action_refused"
    retryable = False


class VerificationFailed(ThursdayError):
    """The action ran but its effect could not be observed (§20, §76)."""

    code = "verification_failed"


class BudgetExceeded(ThursdayError):
    code = "budget_exceeded"


class ProviderError(ThursdayError):
    code = "provider_error"
    retryable = True


class ToolNotFound(ThursdayError):
    code = "tool_not_found"


class AgentNotFound(ThursdayError):
    code = "agent_not_found"


class SecretLeakBlocked(ThursdayError):
    """A payload carrying credential material was stopped before egress (§35)."""

    code = "secret_leak_blocked"


class RateLimited(ThursdayError):
    """Too many requests to this surface, too quickly (§49, §128).

    Retryable, and the only error in this taxonomy for which that is a statement about time
    rather than about the request: nothing is wrong with it, there have merely been too many
    like it. `retry_after_s` says how long the caller should wait, so a client does not have
    to guess and then guess wrong in the direction that keeps the limit tripped.
    """

    code = "rate_limited"
    retryable = True

    def __init__(self, message: str, *, retry_after_s: float, **details: Any) -> None:
        super().__init__(message, retry_after_s=round(retry_after_s, 3), **details)
        self.retry_after_s = retry_after_s


class PrivacyViolation(ThursdayError):
    """A SECRET-class payload was routed somewhere it may not go (§34)."""

    code = "privacy_violation"
