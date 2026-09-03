"""Coding Agent (§15, V9).

Reads code and proposes a change. It does not apply one.

That is the entire design decision and it is worth being explicit about, because the
temptation runs the other way: an agent that edits files directly is more impressive in a
demo and strictly worse to live with. A proposed patch can be read before it lands. An
applied patch is read afterwards, if at all, and the owner's first sight of it is often a
test failing for a reason they now have to reconstruct.

So this agent's ceiling is READ and its output is a **patch**: the file, the change, and the
reasoning. Applying it is `file.write`, which the owner asks for separately and which passes
the permission engine like any other write — including the remote-command escalation if the
file lives on another machine (ADR 0024).

The other rule it keeps: it does not run anything. `shell.run` is ASK_ALWAYS by policy, and
an agent that shelled out to "just check the tests pass" would be asking the owner to
approve arbitrary execution on the strength of a summary they cannot verify. Running tests
is a thing the owner does, or a thing a skill does with its steps written down.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from thursday_shared.enums import DataSensitivity, ModelTier, PermissionLevel
from thursday_shared.models import (
    AgentResult,
    AgentSpec,
    Budget,
    JobContract,
    LLMMessage,
    LLMRequest,
    ToolCall,
)

from thursday_agents.base import BaseAgent

#: Beyond this the file is summarised rather than sent whole. A model given forty thousand
#: lines and asked for a small change returns a small change to the part it still remembers.
MAX_CHARS = 24_000

#: Suffixes this agent will read. Not a security boundary — the path jail and the permission
#: engine are — but a scope: an agent asked to change "the config" should not be reading a
#: private key because it happens to sit in the same folder.
CODE_SUFFIXES = (
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".rs",
    ".go",
    ".java",
    ".kt",
    ".rb",
    ".sh",
    ".sql",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".md",
    ".css",
    ".html",
)


@dataclass(frozen=True)
class Patch:
    """A proposed change, as text. Applying it is a separate, separately-approved act."""

    path: str
    rationale: str
    body: str

    def describe(self) -> str:
        return f"{self.path}: {self.rationale}"


def looks_like_code(path: str) -> bool:
    return path.lower().endswith(CODE_SUFFIXES)


class CodingAgent(BaseAgent):
    spec = AgentSpec(
        name="coding",
        description="Reads code and proposes a change; never applies or runs one.",
        capabilities=["code", "review", "refactor", "explain", "debug"],
        tools=["file.read", "file.search"],
        agent_type="specialist",
        supported_input=["path", "question"],
        supported_output=["patch", "explanation"],
        output_schema={"patch": "string?", "explanation": "string", "path": "string"},
        # READ. The patch is the deliverable; writing it is the owner's next instruction.
        permission_ceiling=PermissionLevel.READ,
        default_budget=Budget(seconds=120, tool_calls=6, usd=0.08),
        model_tier=ModelTier.REASONING,
        cost_profile="expensive",
        latency_profile="slow",
        # Source code is the owner's work and often their employer's. It may go to a cloud
        # model, but never when the context is classified SECRET — the router enforces that.
        privacy_profile="local_preferred",
        system_prompt=(
            "You read code and propose one focused change. Show the change as a complete "
            "replacement for the region you are changing, and say why. If the file does "
            "not need changing, say that instead of inventing an edit. Never claim to have "
            "run anything."
        ),
    )

    async def execute(self, contract: JobContract, ctx: Any) -> AgentResult:
        path = str(contract.inputs.get("path") or "")
        question = str(contract.inputs.get("question") or contract.objective)

        if not path:
            return AgentResult(
                agent=self.spec.name,
                ok=False,
                output={"patch": None, "explanation": "", "path": ""},
                error="no file was named, and I will not guess which one to change",
                summary="no file to read",
            )
        if not looks_like_code(path):
            return AgentResult(
                agent=self.spec.name,
                ok=False,
                output={"patch": None, "explanation": "", "path": path},
                error=f"{path} does not look like source I should be reading",
                summary="not a source file",
            )

        read = await ctx.call_tool(
            ToolCall(
                tool="file.read", args={"path": path}, reason=f"read {path} to answer: {question}"
            )
        )
        if not read.ok:
            return AgentResult(
                agent=self.spec.name,
                ok=False,
                output={"patch": None, "explanation": "", "path": path},
                error=read.error or f"could not read {path}",
                summary=f"could not read {path}",
            )

        source = str(read.data.get("content", ""))
        truncated = len(source) > MAX_CHARS
        response = await ctx.think(
            LLMRequest(
                messages=[
                    LLMMessage(role="system", content=self.spec.system_prompt),
                    LLMMessage(
                        role="user",
                        content=(
                            f"File: {path}\n"
                            f"Request: {question}\n"
                            + (
                                "NOTE: this file was truncated; say so if the change may "
                                "depend on the part you cannot see.\n"
                                if truncated
                                else ""
                            )
                            + f"\n```\n{source[:MAX_CHARS]}\n```"
                        ),
                    ),
                ],
                tier=ModelTier.REASONING,
                sensitivity=DataSensitivity.PRIVATE,
                max_tokens=1800,
            )
        )

        explanation = response.text.strip()
        patch = _extract_patch(explanation)
        return AgentResult(
            agent=self.spec.name,
            ok=True,
            output={
                # None when the model proposed no change. A patch field holding prose is
                # worse than an empty one: something downstream will try to apply it.
                "patch": patch,
                "explanation": explanation,
                "path": path,
                "truncated": truncated,
                # Stated in the output so nothing downstream can mistake a proposal for a
                # change that happened.
                "applied": False,
            },
            summary=(
                f"proposed a change to {path}" if patch else f"read {path}; no change proposed"
            ),
            evidence=[{"path": path, "bytes": len(source), "truncated": truncated}],
        )


def _extract_patch(text: str) -> str | None:
    """The fenced code block from a reply, or None.

    None rather than the whole reply when there is no block: a `patch` field holding prose
    is worse than an empty one, because something downstream will eventually try to write
    it to a file.
    """
    if "```" not in text:
        return None
    parts = text.split("```")
    if len(parts) < 3:
        return None
    block = parts[1]
    # Drop a language tag on the opening fence.
    first, _, rest = block.partition("\n")
    body = rest if first.strip().isalpha() or not first.strip() else block
    return body.strip() or None
