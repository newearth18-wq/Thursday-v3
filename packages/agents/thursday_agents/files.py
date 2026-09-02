"""File Agent (§15, V9).

The computer agent already runs one file command at a time. This one does the jobs that are
*about a set of files* — find the duplicates, tell me what is in this folder, which of these
is the newest — where the answer comes from comparing many files rather than reading one.

The line between the two is not "files": it is whether the work is a command or a question.
"Open this spreadsheet" is a command and belongs next door. "Which of these forty
spreadsheets did I touch last week" is a question, and answering it means walking a
directory, sorting, grouping, and reporting — none of which is a device action.

Its permission ceiling is **READ**, and that is the design rather than a default. Every
question this agent answers is answerable by looking. Deleting the duplicates it found is a
separate instruction the owner gives afterwards, and it goes through the ordinary approval
path like any other deletion (§40, ADR 0008). An agent that could both find duplicates and
remove them would be one bad grouping away from deleting the only copy of something.
"""

from __future__ import annotations

import posixpath
from collections import defaultdict
from typing import Any

from thursday_shared.enums import ModelTier, PermissionLevel
from thursday_shared.models import (
    AgentResult,
    AgentSpec,
    Budget,
    JobContract,
    ToolCall,
)

from thursday_agents.base import BaseAgent

#: How many files one question may examine. A folder with fifty thousand files in it is not
#: a question this agent should silently spend ten minutes on.
MAX_FILES = 500


def group_duplicates(files: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Files that are the same file, grouped.

    Grouped by content hash where the node supplied one, and by (name, size) otherwise —
    and the difference is stated in the result, because they are different claims. A hash
    match means the bytes are identical. A name-and-size match means two files look alike,
    which is a good reason to *show* someone a pair and a bad reason to call them copies.
    """
    by_key: dict[tuple[str, Any], list[dict[str, Any]]] = defaultdict(list)
    for entry in files:
        digest = entry.get("sha256") or entry.get("hash")
        key = (
            ("sha256", digest) if digest else ("name+size", (entry.get("name"), entry.get("size")))
        )
        by_key[key].append(entry)
    return [group for group in by_key.values() if len(group) > 1]


class FileAgent(BaseAgent):
    spec = AgentSpec(
        name="file",
        description="Answers questions about sets of files: what is here, what is newest, what is duplicated.",
        capabilities=["file", "organise", "search", "inventory", "deduplicate"],
        tools=["file.list", "file.search", "file.read"],
        agent_type="specialist",
        supported_input=["path", "pattern"],
        supported_output=["files", "groups", "summary"],
        output_schema={"files": "list", "count": "int", "summary": "string"},
        # READ, deliberately. See the module docstring: finding is this agent's job and
        # deleting is the owner's decision, taken separately and approved separately.
        permission_ceiling=PermissionLevel.READ,
        default_budget=Budget(seconds=90, tool_calls=8, usd=0.02),
        model_tier=ModelTier.FAST,
        cost_profile="cheap",
        latency_profile="fast",
        privacy_profile="local_preferred",
        system_prompt=(
            "You answer questions about sets of files by looking at them. You never modify, "
            "move or delete anything."
        ),
    )

    async def execute(self, contract: JobContract, ctx: Any) -> AgentResult:
        root = str(contract.inputs.get("root") or contract.inputs.get("path") or "~")
        pattern = str(contract.inputs.get("pattern") or "*")
        wants = str(contract.inputs.get("question") or contract.objective).lower()

        found = await ctx.call_tool(
            ToolCall(
                tool="file.search",
                args={"root": root, "pattern": pattern, "limit": MAX_FILES},
                reason="inventory a folder to answer a question about it",
            )
        )
        if not found.ok:
            return AgentResult(
                agent=self.spec.name,
                ok=False,
                output={"files": [], "count": 0, "summary": ""},
                error=found.error or f"could not read {root}",
                summary=f"could not look inside {root}",
            )

        files = [f for f in found.data.get("files", []) if isinstance(f, dict)]
        # The node reports when it stopped early. Passing that on matters: "no duplicates"
        # over a truncated listing is a different statement from "no duplicates".
        truncated = bool(found.data.get("truncated"))

        output: dict[str, Any] = {
            "files": files,
            "count": len(files),
            "truncated": truncated,
            "root": root,
        }

        if any(word in wants for word in ("duplicate", "ซ้ำ", "dedup", "copies")):
            groups = group_duplicates(files)
            output["groups"] = [[f.get("path") for f in group] for group in groups]
            output["exact"] = all(f.get("sha256") or f.get("hash") for f in files)
            summary = (
                f"{len(groups)} group(s) of files that look like copies, across {len(files)} files"
                if groups
                else f"no repeated files among the {len(files)} I could see"
            )
            if groups and not output["exact"]:
                # Said out loud, not buried in a field: "these look alike" and "these are
                # byte-for-byte identical" lead to very different next actions.
                summary += " (matched on name and size, not on content)"
        elif any(word in wants for word in ("newest", "latest", "ล่าสุด", "recent")):
            ordered = sorted(files, key=lambda f: str(f.get("modified") or ""), reverse=True)
            output["files"] = ordered
            newest = ordered[0] if ordered else None
            summary = (
                f"the most recently modified is {newest.get('path')}"
                if newest
                else f"nothing matching {pattern} in {root}"
            )
        else:
            summary = f"{len(files)} file(s) matching {pattern} in {root}"

        if truncated:
            summary += f" — I stopped at {MAX_FILES}, so this is not the whole folder"

        output["summary"] = summary
        return AgentResult(
            agent=self.spec.name,
            ok=True,
            output=output,
            summary=summary,
            evidence=[{"root": root, "pattern": pattern, "examined": len(files)}],
        )


def relative_to(root: str, path: str) -> str:
    """A path as the owner would say it, not as the filesystem stores it."""
    try:
        return posixpath.relpath(path, root)
    except ValueError:
        return path
