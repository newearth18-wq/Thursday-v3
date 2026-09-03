"""Approval service (§38).

An approval that expires silently must fail closed, and a granted approval must not become
a standing permission unless the user chose that scope explicitly.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

from thursday_shared.enums import ApprovalScope, ApprovalState, NotificationPriority
from thursday_shared.errors import PermissionDenied
from thursday_shared.models import ApprovalRequest, Event, PermissionGrant

from thursday_security.permissions import PermissionEngine


class ApprovalService:
    def __init__(
        self,
        permissions: PermissionEngine,
        bus: object | None = None,
        *,
        default_ttl_s: float = 300.0,
    ) -> None:
        self._permissions = permissions
        self._bus = bus
        self._ttl = default_ttl_s
        self._pending: dict[UUID, ApprovalRequest] = {}
        self._waiters: dict[UUID, asyncio.Future[ApprovalRequest]] = {}

    async def request(self, approval: ApprovalRequest) -> ApprovalRequest:
        """Register a pending approval and announce it. Does not block."""
        approval.expires_at = approval.expires_at or datetime.now(UTC) + timedelta(
            seconds=self._ttl
        )
        approval.consequence_of_refusal = approval.consequence_of_refusal or (
            "the task stops here and nothing is changed"
        )
        self._pending[approval.id] = approval
        if self._bus is not None:
            await self._bus.publish(  # type: ignore[attr-defined]
                Event(
                    kind="approval.required",
                    task_id=approval.task_id,
                    device_id=approval.device_id,
                    priority=NotificationPriority.IMPORTANT,
                    payload=approval.model_dump(mode="json"),
                )
            )
        return approval

    async def wait_for(self, approval_id: UUID, *, timeout: float | None = None) -> ApprovalRequest:
        """Await a decision. A timeout is a rejection, never an implied yes."""
        approval = self._pending.get(approval_id)
        if approval is None:
            raise PermissionDenied("unknown approval", approval_id=str(approval_id))
        if approval.state is not ApprovalState.PENDING:
            return approval

        loop = asyncio.get_running_loop()
        future: asyncio.Future[ApprovalRequest] = loop.create_future()
        self._waiters[approval_id] = future
        remaining = timeout
        if remaining is None and approval.expires_at:
            remaining = max(0.0, (approval.expires_at - datetime.now(UTC)).total_seconds())
        try:
            return await asyncio.wait_for(future, timeout=remaining)
        except TimeoutError:
            approval.state = ApprovalState.EXPIRED
            approval.decided_at = datetime.now(UTC)
            await self._announce(approval, "approval.expired")
            return approval
        finally:
            self._waiters.pop(approval_id, None)

    async def decide(
        self,
        approval_id: UUID,
        *,
        approve: bool,
        scope: ApprovalScope = ApprovalScope.ONCE,
        note: str | None = None,
    ) -> ApprovalRequest:
        approval = self._pending.get(approval_id)
        if approval is None:
            raise PermissionDenied("unknown approval", approval_id=str(approval_id))
        if approval.state is not ApprovalState.PENDING:
            return approval
        if approval.expires_at and approval.expires_at <= datetime.now(UTC):
            approval.state = ApprovalState.EXPIRED
            await self._announce(approval, "approval.expired")
            return approval

        # An ASK_ALWAYS action is asked every time. Silently downgrading the scope here —
        # rather than erroring — means an API client passing scope=always still gets a
        # one-time approval, and no standing grant is ever created (ADR 0008).
        if scope not in approval.scopes_offered:
            scope = ApprovalScope.ONCE

        approval.state = ApprovalState.APPROVED if approve else ApprovalState.REJECTED
        approval.scope = scope
        approval.note = note
        approval.decided_at = datetime.now(UTC)

        if approve and scope in (ApprovalScope.SESSION, ApprovalScope.ALWAYS):
            self._permissions.add_grant(
                PermissionGrant(
                    action=approval.action,
                    resource_glob=_scope_glob(approval.resource),
                    device_id=approval.device_id,
                    scope=scope,
                    expires_at=(
                        datetime.now(UTC) + timedelta(hours=8)
                        if scope is ApprovalScope.SESSION
                        else datetime.now(UTC) + timedelta(days=30)
                    ),
                )
            )

        await self._announce(approval, "approval.granted" if approve else "approval.denied")
        if (waiter := self._waiters.get(approval_id)) is not None and not waiter.done():
            waiter.set_result(approval)
        return approval

    def pending(self) -> list[ApprovalRequest]:
        now = datetime.now(UTC)
        for approval in self._pending.values():
            if (
                approval.state is ApprovalState.PENDING
                and approval.expires_at
                and approval.expires_at <= now
            ):
                approval.state = ApprovalState.EXPIRED
        return [a for a in self._pending.values() if a.state is ApprovalState.PENDING]

    def get(self, approval_id: UUID) -> ApprovalRequest | None:
        return self._pending.get(approval_id)

    async def _announce(self, approval: ApprovalRequest, kind: str) -> None:
        if self._bus is None:
            return
        await self._bus.publish(  # type: ignore[attr-defined]
            Event(kind=kind, task_id=approval.task_id, payload=approval.model_dump(mode="json"))
        )


def _scope_glob(resource: str) -> str:
    """Derive a *narrow* glob from the approved resource.

    "Always allow" on one file must not become "always allow" on the filesystem, so the
    glob is capped at the parent directory (or the exact value for non-paths).
    """
    if not resource:
        return "*"
    if "/" in resource or "\\" in resource:
        sep = "/" if "/" in resource else "\\"
        parent = resource.rsplit(sep, 1)[0]
        return f"{parent}{sep}*"
    return resource
