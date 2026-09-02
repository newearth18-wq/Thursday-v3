"""Built-in tools.

Each is a thin, declared, permission-checked wrapper. Tools do not decide *whether* they may
run — the Permission Engine does that before they are called — but they do declare enough
for that decision to be made well.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from thursday_shared.enums import (
    ControlTier,
    DataSensitivity,
    MemoryLayer,
    PermissionLevel,
    RiskLevel,
)
from thursday_shared.errors import DeviceUnavailable
from thursday_shared.models import (
    DeviceAction,
    MemoryQuery,
    MemoryWrite,
    ToolCall,
    ToolResult,
    ToolSpec,
)


class DeviceActionTool:
    """Bridges one node action into the tool system, preserving its verification result."""

    def __init__(self, action_name: str, hub: object, spec: ToolSpec) -> None:
        self.action_name = action_name
        self.spec = spec
        self._hub = hub

    async def run(self, call: ToolCall, ctx: Any) -> ToolResult:
        started = time.perf_counter()
        if call.device_id is None:
            raise DeviceUnavailable(f"{self.spec.name} needs a target device")
        result = await self._hub.invoke(  # type: ignore[attr-defined]
            call.device_id,
            DeviceAction(
                action=self.action_name,
                args=call.args,
                task_id=call.task_id,
                step_id=call.step_id,
                reason=call.reason,
            ),
        )
        return ToolResult(
            call_id=call.id,
            tool=self.spec.name,
            ok=result.ok,
            verified=result.verified,
            data=result.data,
            evidence=result.evidence,
            error=result.error,
            duration_ms=(time.perf_counter() - started) * 1000,
            undo=result.undo,
        )


class MemorySearchTool:
    spec = ToolSpec(
        name="memory.search",
        description="Search Thursday's layered memory for what is already known.",
        capabilities=["recall", "search"],
        permission=PermissionLevel.READ,
        risk=RiskLevel.NONE,
        latency_ms=30,
        local_only=True,
        input_schema={"query": "string", "layers": "list[str]?", "k": "int?"},
        output_schema={"memories": "list[{content, layer, confidence, source, score}]"},
    )

    def __init__(self, memory: object) -> None:
        self._memory = memory

    async def run(self, call: ToolCall, ctx: Any) -> ToolResult:
        started = time.perf_counter()
        layers = [MemoryLayer(v) for v in call.args.get("layers", [])]
        records = await self._memory.recall(  # type: ignore[attr-defined]
            MemoryQuery(
                text=str(call.args.get("query", "")), layers=layers, k=int(call.args.get("k", 6))
            )
        )
        return ToolResult(
            call_id=call.id,
            tool=self.spec.name,
            ok=True,
            verified=True,
            data={
                "memories": [
                    {
                        "content": r.content,
                        "layer": str(r.layer),
                        "confidence": r.confidence,
                        "source": str(r.source),
                        "score": round(r.score or 0.0, 3),
                    }
                    for r in records
                ]
            },
            duration_ms=(time.perf_counter() - started) * 1000,
        )


class MemoryWriteTool:
    spec = ToolSpec(
        name="memory.write",
        description="Record something durable. The write policy may still decline it.",
        capabilities=["remember"],
        permission=PermissionLevel.MODIFY,
        risk=RiskLevel.LOW,
        latency_ms=40,
        local_only=True,
        input_schema={"content": "string", "layer": "string", "importance": "float?"},
        output_schema={"written": "bool", "memory_id": "string?"},
    )

    def __init__(self, memory: object) -> None:
        self._memory = memory

    async def run(self, call: ToolCall, ctx: Any) -> ToolResult:
        started = time.perf_counter()
        record = await self._memory.write(  # type: ignore[attr-defined]
            MemoryWrite(
                layer=MemoryLayer(call.args.get("layer", "semantic")),
                content=str(call.args["content"]),
                key=call.args.get("key"),
                importance=float(call.args.get("importance", 0.5)),
                task_id=call.task_id,
            )
        )
        return ToolResult(
            call_id=call.id,
            tool=self.spec.name,
            ok=True,
            verified=record is not None,
            data={"written": record is not None, "memory_id": str(record.id) if record else None},
            duration_ms=(time.perf_counter() - started) * 1000,
        )


class ObsidianWriteTool:
    spec = ToolSpec(
        name="obsidian.write",
        description="Write a Markdown note into the human-readable vault.",
        capabilities=["note", "document"],
        permission=PermissionLevel.MODIFY,
        risk=RiskLevel.LOW,
        latency_ms=25,
        local_only=True,
        input_schema={"folder": "string", "title": "string", "body": "string"},
        output_schema={"path": "string"},
    )

    def __init__(self, vault: object) -> None:
        self._vault = vault

    async def run(self, call: ToolCall, ctx: Any) -> ToolResult:
        started = time.perf_counter()
        path = self._vault.write_note(  # type: ignore[attr-defined]
            folder=str(call.args.get("folder", "00 Inbox")),
            title=str(call.args["title"]),
            body=str(call.args["body"]),
            frontmatter=call.args.get("frontmatter"),
        )
        return ToolResult(
            call_id=call.id,
            tool=self.spec.name,
            ok=path is not None,
            verified=bool(path and path.exists()),
            data={"path": str(path) if path else None},
            evidence={"exists": bool(path and path.exists())},
            duration_ms=(time.perf_counter() - started) * 1000,
        )


class ObsidianSearchTool:
    spec = ToolSpec(
        name="obsidian.search",
        description="Search the Obsidian vault for notes containing a phrase.",
        capabilities=["search", "note"],
        permission=PermissionLevel.READ,
        risk=RiskLevel.NONE,
        latency_ms=60,
        local_only=True,
        input_schema={"query": "string", "limit": "int?"},
        output_schema={"hits": "list[{path, excerpt}]"},
    )

    def __init__(self, vault: object) -> None:
        self._vault = vault

    async def run(self, call: ToolCall, ctx: Any) -> ToolResult:
        started = time.perf_counter()
        hits = self._vault.search(
            str(call.args.get("query", "")), limit=int(call.args.get("limit", 10))
        )  # type: ignore[attr-defined]
        return ToolResult(
            call_id=call.id,
            tool=self.spec.name,
            ok=True,
            verified=True,
            data={"hits": [{"path": str(p), "excerpt": e} for p, e in hits]},
            duration_ms=(time.perf_counter() - started) * 1000,
        )


class ClockTool:
    spec = ToolSpec(
        name="clock.now",
        description="Current date and time, for deadline and scheduling reasoning.",
        capabilities=["time"],
        permission=PermissionLevel.READ,
        risk=RiskLevel.NONE,
        latency_ms=1,
        local_only=True,
        output_schema={"iso": "string", "weekday": "string"},
    )

    async def run(self, call: ToolCall, ctx: Any) -> ToolResult:
        now = datetime.now(UTC)
        return ToolResult(
            call_id=call.id,
            tool=self.spec.name,
            ok=True,
            verified=True,
            data={"iso": now.isoformat(), "weekday": now.strftime("%A"), "epoch": now.timestamp()},
        )


class WebSearchTool:
    """Placeholder for the Phase-2 research connector.

    It is registered so the Research Agent's routing, permissions and budget accounting are
    exercised from day one — and it fails honestly rather than fabricating results.
    """

    spec = ToolSpec(
        name="web.search",
        description="Search the public web. Requires network access and a configured provider.",
        capabilities=["search", "research"],
        permission=PermissionLevel.READ,
        control_tier=ControlTier.API,
        risk=RiskLevel.LOW,
        cost_usd=0.005,
        latency_ms=1200,
        max_sensitivity=DataSensitivity.INTERNAL,
        input_schema={"query": "string", "k": "int?"},
        output_schema={"results": "list[{title, url, snippet}]"},
    )

    def __init__(self, *, provider: object | None = None) -> None:
        self._provider = provider

    async def run(self, call: ToolCall, ctx: Any) -> ToolResult:
        if self._provider is None:
            return ToolResult(
                call_id=call.id,
                tool=self.spec.name,
                ok=False,
                verified=False,
                error="no web search provider is configured; running offline",
            )
        results = await self._provider.search(str(call.args["query"]), k=int(call.args.get("k", 5)))  # type: ignore[attr-defined]
        return ToolResult(
            call_id=call.id,
            tool=self.spec.name,
            ok=True,
            verified=True,
            data={"results": results},
            cost_usd=self.spec.cost_usd,
        )


#: Device actions promoted to tools, with the spec the router reasons over (PART 16).
#: Names match the node catalogue exactly, so a tool call is a device command.
DEVICE_TOOL_SPECS: dict[str, ToolSpec] = {
    "app.open": ToolSpec(
        name="app.open",
        description="Launch an application on a device and verify it started.",
        capabilities=["app_control", "open", "os"],
        permission=PermissionLevel.OPEN,
        control_tier=ControlTier.OS_API,
        risk=RiskLevel.LOW,
        latency_ms=1500,
        requires_device=True,
        local_only=True,
        supports_undo=True,
        input_schema={"app": "string"},
        output_schema={"app": "string", "pid": "int?"},
    ),
    "app.close": ToolSpec(
        name="app.close",
        description="Close an application on a device.",
        capabilities=["app_control", "os"],
        permission=PermissionLevel.MODIFY,
        control_tier=ControlTier.OS_API,
        risk=RiskLevel.LOW,
        latency_ms=900,
        requires_device=True,
        local_only=True,
        supports_undo=True,
        input_schema={"app": "string"},
    ),
    "file.open": ToolSpec(
        name="file.open",
        description="Open a file with its registered handler.",
        capabilities=["file", "open"],
        permission=PermissionLevel.OPEN,
        control_tier=ControlTier.OS_API,
        latency_ms=1200,
        requires_device=True,
        local_only=True,
        input_schema={"path": "string"},
    ),
    "file.read": ToolSpec(
        name="file.read",
        description="Read a text file from a device.",
        capabilities=["file", "read"],
        permission=PermissionLevel.READ,
        control_tier=ControlTier.OS_API,
        latency_ms=80,
        requires_device=True,
        local_only=True,
        input_schema={"path": "string"},
    ),
    "file.write": ToolSpec(
        name="file.write",
        description="Write text to a file, versioning any existing copy, and verify it.",
        capabilities=["file", "write"],
        permission=PermissionLevel.MODIFY,
        control_tier=ControlTier.OS_API,
        risk=RiskLevel.MEDIUM,
        latency_ms=120,
        requires_device=True,
        local_only=True,
        supports_undo=True,
        supports_dry_run=True,
        input_schema={"path": "string", "content": "string"},
    ),
    "file.list": ToolSpec(
        name="file.list",
        description="List the contents of a directory.",
        capabilities=["file", "read"],
        permission=PermissionLevel.READ,
        control_tier=ControlTier.OS_API,
        latency_ms=60,
        requires_device=True,
        local_only=True,
        input_schema={"path": "string"},
    ),
    "file.search": ToolSpec(
        name="file.search",
        description="Find files by one or more glob patterns, newest first. Read-only.",
        capabilities=["file", "search"],
        permission=PermissionLevel.READ,
        control_tier=ControlTier.OS_API,
        latency_ms=400,
        requires_device=True,
        local_only=True,
        input_schema={"root": "string", "pattern": "string|list", "limit": "int?"},
    ),
    "file.move": ToolSpec(
        name="file.move",
        description="Move or rename a path.",
        capabilities=["file", "write"],
        permission=PermissionLevel.MODIFY,
        control_tier=ControlTier.OS_API,
        risk=RiskLevel.MEDIUM,
        latency_ms=100,
        requires_device=True,
        local_only=True,
        supports_undo=True,
        supports_dry_run=True,
        input_schema={"src": "string", "dst": "string"},
    ),
    "file.delete": ToolSpec(
        name="file.delete",
        description="Delete a path (quarantined, so it stays recoverable).",
        capabilities=["file", "delete"],
        permission=PermissionLevel.MODIFY,
        control_tier=ControlTier.OS_API,
        risk=RiskLevel.HIGH,
        latency_ms=100,
        requires_device=True,
        local_only=True,
        reversible=True,
        supports_undo=True,
        supports_dry_run=True,
        input_schema={"path": "string"},
    ),
    "screen.capture": ToolSpec(
        name="screen.capture",
        description="Capture the screen of a device.",
        capabilities=["screen", "vision"],
        permission=PermissionLevel.OPEN,
        control_tier=ControlTier.OS_API,
        latency_ms=600,
        requires_device=True,
        local_only=True,
    ),
    "window.active": ToolSpec(
        name="window.active",
        description="Report the focused window title.",
        capabilities=["screen"],
        permission=PermissionLevel.READ,
        control_tier=ControlTier.OS_API,
        latency_ms=200,
        requires_device=True,
        local_only=True,
    ),
    "system.process.list": ToolSpec(
        name="system.process.list",
        description="Check whether a process is running on a device.",
        capabilities=["app_control", "read", "diagnostics"],
        permission=PermissionLevel.READ,
        control_tier=ControlTier.OS_API,
        latency_ms=250,
        requires_device=True,
        local_only=True,
        input_schema={"name": "string"},
    ),
    "system.info": ToolSpec(
        name="system.info",
        description="Report a device's OS, CPU, memory and disk.",
        capabilities=["read", "diagnostics", "os"],
        permission=PermissionLevel.READ,
        control_tier=ControlTier.OS_API,
        latency_ms=300,
        requires_device=True,
        local_only=True,
    ),
    "shell.run": ToolSpec(
        name="shell.run",
        description="Run a shell command. High risk; approval required every time.",
        capabilities=["shell", "os"],
        permission=PermissionLevel.MODIFY,
        control_tier=ControlTier.OS_API,
        risk=RiskLevel.HIGH,
        latency_ms=800,
        requires_device=True,
        local_only=True,
        reversible=False,
        supports_dry_run=True,
        input_schema={"command": "string"},
    ),
    "browser.open": ToolSpec(
        name="browser.open",
        description="Open a URL in the device's default browser.",
        capabilities=["browser", "web", "open"],
        permission=PermissionLevel.OPEN,
        control_tier=ControlTier.BROWSER,
        latency_ms=1500,
        requires_device=True,
        local_only=True,
        input_schema={"url": "string"},
    ),
    "notify.show": ToolSpec(
        name="notify.show",
        description="Show a notification on a device.",
        capabilities=["notify"],
        permission=PermissionLevel.OPEN,
        control_tier=ControlTier.OS_API,
        latency_ms=150,
        requires_device=True,
        local_only=True,
        input_schema={"title": "string", "body": "string"},
    ),
}


def register_builtin_tools(
    registry: object,
    *,
    hub: object,
    memory: object,
    vault: object,
    web_search_provider: object | None = None,
) -> None:
    """Wire the standard toolset. Called once by the DI container."""
    for action_name, spec in DEVICE_TOOL_SPECS.items():
        registry.register(DeviceActionTool(action_name, hub, spec))  # type: ignore[attr-defined]
    registry.register(MemorySearchTool(memory))  # type: ignore[attr-defined]
    registry.register(MemoryWriteTool(memory))  # type: ignore[attr-defined]
    registry.register(ObsidianWriteTool(vault))  # type: ignore[attr-defined]
    registry.register(ObsidianSearchTool(vault))  # type: ignore[attr-defined]
    registry.register(ClockTool())  # type: ignore[attr-defined]
    registry.register(WebSearchTool(provider=web_search_provider))  # type: ignore[attr-defined]
