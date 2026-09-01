"""Computer Agent (§15, §20).

Operates the machine through the node protocol using the SEE → THINK → ACT → VERIFY loop.
It reports what it *observed*, and when it could not observe the effect it says so — the
caller (and ultimately Thursday) must never be able to mistake dispatch for success.
"""

from __future__ import annotations

from typing import Any

from thursday_shared.enums import ModelTier, PermissionLevel
from thursday_shared.models import AgentResult, AgentSpec, Budget, JobContract, ToolCall

from thursday_agents.base import BaseAgent


class ComputerAgent(BaseAgent):
    spec = AgentSpec(
        name="computer",
        description="Operates the user's machines: applications, files, processes, screen.",
        capabilities=[
            "app_control",
            "file",
            "os",
            "open",
            "read",
            "write",
            "diagnostics",
            "screen",
        ],
        tools=[
            "open_app",
            "close_app",
            "open_file",
            "read_file",
            "write_file",
            "list_dir",
            "search_files",
            "process_status",
            "system_info",
            "read_active_window",
            "screenshot",
        ],
        permission_ceiling=PermissionLevel.MODIFY,
        default_budget=Budget(seconds=60, tool_calls=8, usd=0.02),
        model_tier=ModelTier.FAST,
        system_prompt=(
            "You operate a computer on the owner's behalf. Prefer APIs and application "
            "integration over GUI control. After every action, verify the effect before "
            "reporting it."
        ),
    )

    async def execute(self, contract: JobContract, ctx: Any) -> AgentResult:
        action = str(contract.inputs.get("action", ""))
        args = dict(contract.inputs.get("args", {}))
        if not action:
            return AgentResult(
                agent=self.spec.name, ok=False, error="no action supplied in the contract"
            )

        # SEE — read the machine's state before acting, so verification has a baseline.
        before = await self._observe(action, args, ctx)

        # ACT
        result = await ctx.call_tool(
            ToolCall(
                tool=action,
                args=args,
                task_id=contract.task_id,
                step_id=contract.step_id,
                reason=contract.objective,
            )
        )
        if not result.ok:
            return AgentResult(
                agent=self.spec.name,
                ok=False,
                error=result.error or f"{action} failed",
                tool_results=[result],
                evidence=[{"before": before}],
            )

        # VERIFY — an independent second look, not a re-read of the same return value.
        after = await self._observe(action, args, ctx)
        confirmed = result.verified or self._changed(action, before, after)

        summary = self._summarise(action, args, result.data, confirmed)
        return AgentResult(
            agent=self.spec.name,
            ok=True,
            output={
                "action": action,
                "verified": confirmed,
                "evidence": result.evidence,
                **result.data,
            },
            summary=summary,
            evidence=[{"before": before, "after": after, "node_evidence": result.evidence}],
            tool_results=[result],
        )

    async def _observe(self, action: str, args: dict, ctx: Any) -> dict:
        """The SEE step. Cheap, read-only, and specific to what the action will change."""
        try:
            if action in ("open_app", "close_app") and args.get("name"):
                probe = await ctx.call_tool(
                    ToolCall(tool="process_status", args={"name": args["name"]})
                )
                return {
                    "processes": probe.data.get("processes", []),
                    "running": probe.data.get("running"),
                }
            if action in ("write_file", "delete", "move") and args.get("path"):
                probe = await ctx.call_tool(
                    ToolCall(tool="list_dir", args={"path": _parent_of(str(args["path"]))})
                )
                return {"entries": len(probe.data.get("entries", []))}
        except Exception:
            return {"observed": False}
        return {}

    def _changed(self, action: str, before: dict, after: dict) -> bool:
        if action == "open_app":
            return bool(after.get("running")) and not before.get("running")
        if action == "close_app":
            return bool(before.get("running")) and not after.get("running")
        if "entries" in before and "entries" in after:
            return before["entries"] != after["entries"]
        return False

    def _summarise(self, action: str, args: dict, data: dict, verified: bool) -> str:
        target = args.get("name") or args.get("path") or args.get("root") or ""
        suffix = "" if verified else " (dispatched, but the effect could not be confirmed)"
        if action == "open_app":
            pids = data.get("pid")
            return f"opened {target}" + (f" (pid {pids})" if pids else "") + suffix
        if action == "close_app":
            return f"closed {target}{suffix}"
        if action == "write_file":
            return f"wrote {data.get('bytes', 0)} bytes to {target}{suffix}"
        if action == "list_dir":
            return f"listed {len(data.get('entries', []))} entries in {target}"
        if action == "system_info":
            return f"read system information from {data.get('hostname', 'the device')}"
        return f"{action} on {target}{suffix}".strip()


def _parent_of(path: str) -> str:
    from pathlib import Path

    return str(Path(path).expanduser().parent)
