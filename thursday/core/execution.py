"""Execution context — the narrow surface an agent or tool is given.

An agent gets exactly this object: tools it may call (checked, budgeted, audited), memory
it may read and write, a model it may think with, and an event bus it may report on. It has
no reference to the orchestrator, the permission engine, or the user. That containment is
what makes §96's "agents never hold standing admin" enforceable rather than aspirational.
"""

from __future__ import annotations

import time
from uuid import UUID

from thursday.core.logging import get_logger
from thursday.security.audit import AuditEntry
from thursday.security.redaction import redact_dict
from thursday.shared.enums import DataSensitivity, PolicyDecision
from thursday.shared.errors import ApprovalRequired, PermissionDenied
from thursday.shared.ids import current_trace_id
from thursday.shared.models import (
    ActionRequest,
    ApprovalRequest,
    ContextPackage,
    Event,
    LLMRequest,
    LLMResponse,
    MemoryQuery,
    MemoryRecord,
    MemoryWrite,
    PermissionSet,
    Spend,
    ToolCall,
    ToolResult,
)

log = get_logger(__name__)


class ToolExecutor:
    """The one place a tool is actually invoked.

    Permission check → execute → verify-aware result → undo record → audit → event.
    Nothing else in the system calls ``tool.run`` directly.
    """

    def __init__(
        self,
        *,
        registry: object,
        permissions: object,
        approvals: object,
        audit: object,
        undo: object,
        bus: object,
        tasks: object | None = None,
        approval_timeout_s: float = 300.0,
    ) -> None:
        self._registry = registry
        self._permissions = permissions
        self._approvals = approvals
        self._audit = audit
        self._undo = undo
        self._bus = bus
        self._tasks = tasks
        self._approval_timeout = approval_timeout_s

    async def execute(
        self,
        call: ToolCall,
        *,
        permissions: PermissionSet | None = None,
        sensitivity: DataSensitivity = DataSensitivity.PRIVATE,
        agent: str | None = None,
        device_name: str | None = None,
        object_count: int = 1,
        wait_for_approval: bool = True,
    ) -> ToolResult:
        tool = self._registry.get(call.tool)  # type: ignore[attr-defined]
        spec = tool.spec
        resource = _resource_of(call)

        request = ActionRequest(
            action=call.tool,
            resource=resource,
            device_id=call.device_id,
            agent=agent,
            level=spec.permission,
            risk=spec.risk,
            reversible=spec.reversible,
            object_count=object_count,
            sensitivity=sensitivity,
            task_id=call.task_id,
            step_id=call.step_id,
            expected_outcome=call.reason or spec.description,
        )
        verdict = self._permissions.decide(request, permissions=permissions)  # type: ignore[attr-defined]

        if verdict.decision is PolicyDecision.BLOCK:
            self._audit.record(  # type: ignore[attr-defined]
                AuditEntry(
                    actor="agent" if agent else "thursday",
                    agent=agent,
                    tool=call.tool,
                    action=call.tool,
                    resource=resource,
                    task_id=call.task_id,
                    input_summary=redact_dict(call.args),
                    result="blocked",
                    permission_decision=verdict.decision.value,
                    error=verdict.reason,
                )
            )
            raise PermissionDenied(verdict.reason, tool=call.tool, rule=verdict.rule)

        approval_id: UUID | None = None
        if verdict.decision is PolicyDecision.ASK:
            approval = await self._approvals.request(  # type: ignore[attr-defined]
                ApprovalRequest(
                    action=call.tool,
                    agent=agent,
                    device_id=call.device_id,
                    device_name=device_name,
                    resource=resource,
                    risk=verdict.risk,
                    level=verdict.level,
                    reversible=spec.reversible,
                    expected_outcome=call.reason or spec.description,
                    task_id=call.task_id,
                    step_id=call.step_id,
                )
            )
            approval_id = approval.id
            if not wait_for_approval:
                raise ApprovalRequired(
                    "this action needs your approval", approval_id=str(approval.id), tool=call.tool
                )
            decided = await self._approvals.wait_for(approval.id, timeout=self._approval_timeout)  # type: ignore[attr-defined]
            if decided.state.value != "approved":
                self._audit.record(  # type: ignore[attr-defined]
                    AuditEntry(
                        actor="user",
                        agent=agent,
                        tool=call.tool,
                        action=call.tool,
                        resource=resource,
                        task_id=call.task_id,
                        result="blocked",
                        permission_decision=f"ASK/{decided.state.value}",
                        approval_id=approval.id,
                    )
                )
                raise PermissionDenied(
                    f"the request was {decided.state.value}",
                    tool=call.tool,
                    approval_id=str(approval.id),
                )

        started = time.perf_counter()
        try:
            result = await tool.run(call, self)
        except Exception as exc:
            self._audit.record(  # type: ignore[attr-defined]
                AuditEntry(
                    actor="agent" if agent else "thursday",
                    agent=agent,
                    tool=call.tool,
                    action=call.tool,
                    resource=resource,
                    task_id=call.task_id,
                    input_summary=redact_dict(call.args),
                    result="failed",
                    permission_decision=verdict.decision.value,
                    approval_id=approval_id,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            await self._bus.publish(  # type: ignore[attr-defined]
                Event(
                    kind="tool.failed",
                    task_id=call.task_id,
                    device_id=call.device_id,
                    payload={"tool": call.tool, "error": str(exc)},
                )
            )
            raise

        if result.undo is not None:
            self._undo.record(result.undo)  # type: ignore[attr-defined]

        if self._tasks is not None and call.task_id is not None:
            self._tasks.charge(  # type: ignore[attr-defined]
                call.task_id,
                Spend(tool_calls=1, usd=result.cost_usd, seconds=(time.perf_counter() - started)),
            )

        self._audit.record(  # type: ignore[attr-defined]
            AuditEntry(
                actor="agent" if agent else "thursday",
                agent=agent,
                tool=call.tool,
                action=call.tool,
                resource=resource,
                task_id=call.task_id,
                device_id=call.device_id,
                input_summary=redact_dict(call.args),
                output_summary=redact_dict({"evidence": result.evidence}),
                result="ok"
                if result.ok and result.verified
                else ("failed" if not result.ok else "unverified"),
                permission_decision=verdict.decision.value,
                approval_id=approval_id,
                error=result.error,
            )
        )
        await self._bus.publish(  # type: ignore[attr-defined]
            Event(
                kind="tool.executed",
                task_id=call.task_id,
                device_id=call.device_id,
                payload={
                    "tool": call.tool,
                    "resource": resource,
                    "ok": result.ok,
                    "verified": result.verified,
                    "agent": agent,
                },
            )
        )
        return result


class AgentContext:
    """The ``ExecutionContext`` implementation handed to agents (see shared.interfaces)."""

    def __init__(
        self,
        *,
        executor: ToolExecutor,
        memory: object,
        models: object,
        bus: object,
        task_id: UUID | None = None,
        agent: str | None = None,
        permissions: PermissionSet | None = None,
        context: ContextPackage | None = None,
        device_id: UUID | None = None,
        device_name: str | None = None,
        sensitivity: DataSensitivity = DataSensitivity.PRIVATE,
        offline: bool = False,
    ) -> None:
        self._executor = executor
        self._memory = memory
        self._models = models
        self._bus = bus
        self.task_id = task_id
        self.agent = agent
        self.permissions = permissions or PermissionSet()
        self.context = context
        self.device_id = device_id
        self.device_name = device_name
        self.sensitivity = sensitivity
        self.offline = offline
        self.trace_id = current_trace_id()
        self.spend = Spend()

    async def call_tool(self, call: ToolCall) -> ToolResult:
        if call.device_id is None and self.device_id is not None:
            call = call.model_copy(update={"device_id": self.device_id})
        if call.task_id is None:
            call = call.model_copy(update={"task_id": self.task_id})
        result = await self._executor.execute(
            call,
            permissions=self.permissions,
            sensitivity=self.sensitivity,
            agent=self.agent,
            device_name=self.device_name,
        )
        self.spend.tool_calls += 1
        self.spend.usd += result.cost_usd
        return result

    async def recall(self, query: MemoryQuery) -> list[MemoryRecord]:
        return await self._memory.recall(query)  # type: ignore[attr-defined]

    async def remember(self, write: MemoryWrite) -> MemoryRecord | None:
        if write.task_id is None:
            write = write.model_copy(update={"task_id": self.task_id})
        return await self._memory.write(write)  # type: ignore[attr-defined]

    async def think(self, request: LLMRequest) -> LLMResponse:
        request = request.model_copy(
            update={"sensitivity": max(request.sensitivity, self.sensitivity)}
        )
        response, decision = await self._models.complete(request, offline=self.offline)  # type: ignore[attr-defined]
        self.spend.tokens += response.tokens_in + response.tokens_out
        self.spend.usd += response.cost_usd
        log.debug(
            "agent_thought", agent=self.agent, model=decision.provider_name, tier=str(decision.tier)
        )
        return response

    async def emit(self, event: Event) -> None:
        if event.task_id is None:
            event = event.model_copy(update={"task_id": self.task_id})
        await self._bus.publish(event)  # type: ignore[attr-defined]


def _resource_of(call: ToolCall) -> str:
    """The thing being acted on, for policy scoping and the audit trail."""
    for key in ("path", "src", "name", "root", "url", "to", "title", "command", "query"):
        if (value := call.args.get(key)) not in (None, ""):
            return str(value)
    return ""
