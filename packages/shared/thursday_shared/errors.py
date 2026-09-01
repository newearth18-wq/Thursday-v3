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


class PrivacyViolation(ThursdayError):
    """A SECRET-class payload was routed somewhere it may not go (§34)."""

    code = "privacy_violation"
