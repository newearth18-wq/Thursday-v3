"""Research Agent (§15).

Searches memory first, then the vault, then the web — cheapest and most trustworthy source
first. It attaches provenance to every claim (§74) and, when it has nothing, says so rather
than filling the gap with plausible prose.
"""

from __future__ import annotations

from typing import Any

from thursday.agents.base import BaseAgent
from thursday.shared.enums import DataSensitivity, ModelTier, PermissionLevel
from thursday.shared.models import (
    AgentResult,
    AgentSpec,
    Budget,
    JobContract,
    LLMMessage,
    LLMRequest,
    ToolCall,
)


class ResearchAgent(BaseAgent):
    spec = AgentSpec(
        name="research",
        description="Finds and cross-checks information from memory, the vault and the web.",
        capabilities=["research", "search", "recall", "fact_check", "summarize"],
        tools=["memory_search", "obsidian_search", "web_search"],
        permission_ceiling=PermissionLevel.READ,
        default_budget=Budget(seconds=90, tool_calls=6, usd=0.05),
        model_tier=ModelTier.STANDARD,
        system_prompt=(
            "You research questions for the owner. Cite the source of every claim. "
            "If the available sources do not answer the question, say what is missing "
            "rather than inferring an answer."
        ),
    )

    async def execute(self, contract: JobContract, ctx: Any) -> AgentResult:
        question = str(contract.inputs.get("question") or contract.objective)
        findings: list[dict[str, Any]] = []

        memory_hit = await ctx.call_tool(ToolCall(tool="memory_search", args={"query": question, "k": 5}))
        for record in memory_hit.data.get("memories", []):
            findings.append(
                {"source": f"memory/{record['layer']}", "content": record["content"],
                 "confidence": record["confidence"]}
            )

        vault_hit = await ctx.call_tool(ToolCall(tool="obsidian_search", args={"query": question, "limit": 5}))
        for hit in vault_hit.data.get("hits", []):
            findings.append({"source": f"vault/{hit['path']}", "content": hit["excerpt"], "confidence": 0.8})

        used_web = False
        if len(findings) < 2 and contract.permissions.network:
            web_hit = await ctx.call_tool(ToolCall(tool="web_search", args={"query": question, "k": 5}))
            used_web = web_hit.ok
            for row in web_hit.data.get("results", []):
                findings.append(
                    {"source": row.get("url", "web"), "content": row.get("snippet", ""), "confidence": 0.6}
                )

        if not findings:
            return AgentResult(
                agent=self.spec.name,
                ok=True,
                output={
                    "answer": None,
                    "findings": [],
                    "sources": [],
                    "gap": "no source in memory, the vault, or the web addressed this question",
                },
                summary="found nothing on this in the available sources",
            )

        synthesis = await ctx.think(
            LLMRequest(
                messages=[
                    LLMMessage(role="system", content=self.spec.system_prompt),
                    LLMMessage(
                        role="user",
                        content=(
                            f"Question: {question}\n\nFindings:\n"
                            + "\n".join(f"- [{f['source']}] {f['content']}" for f in findings[:12])
                            + "\n\nAnswer using only these findings, and name the source of each claim."
                        ),
                    ),
                ],
                tier=ModelTier.STANDARD,
                sensitivity=DataSensitivity.INTERNAL if used_web else DataSensitivity.PRIVATE,
                max_tokens=700,
            )
        )
        return AgentResult(
            agent=self.spec.name,
            ok=True,
            output={
                "answer": synthesis.text,
                "findings": findings,
                "sources": sorted({f["source"] for f in findings}),
            },
            summary=f"gathered {len(findings)} findings from {len({f['source'] for f in findings})} sources",
            evidence=findings,
        )
