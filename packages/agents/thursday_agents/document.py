"""Document Agent (§15, V9).

Writes the report at the end of an analysis DAG. Its one hard rule is that every figure in
the document came from a step that computed it — the agent assembles, it does not calculate.

The reason is the same one that keeps arithmetic out of `DataAgent`'s model call, one layer
further on. A report is the artefact a person acts on and forwards; a number that appeared
somewhere between the analysis and the prose is a number nobody can trace and everybody
believes. So the figures are inserted from `contract.upstream`, the model is given them as
fixed text, and `output.sources` names which step each section rests on.

An analysis that did not arrive produces a refusal, not a report with the numbers left out.
A report with the numbers left out still looks like a report.
"""

from __future__ import annotations

import json
from typing import Any

from thursday_shared.enums import DataSensitivity, ModelTier, PermissionLevel
from thursday_shared.models import (
    AgentResult,
    AgentSpec,
    Budget,
    JobContract,
    LLMMessage,
    LLMRequest,
)

from thursday_agents.base import BaseAgent
from thursday_agents.grounding import grounded


def _title_of(contract: JobContract) -> str:
    """What the report is called.

    The plan's objective, not the step's. A step objective names one slot in a job — for a
    skill-derived plan it is literally "document: as demonstrated" — and putting that at the
    top of a document someone forwards is worse than useless.
    """
    return str(contract.context.get("plan") or contract.context.get("task") or contract.objective)


class DocumentAgent(BaseAgent):
    spec = AgentSpec(
        name="document",
        description="Assembles a report from what earlier steps produced, citing each one.",
        capabilities=["document", "report", "write", "summarize", "format"],
        # It writes into the conversation, not onto a disk. Saving the report is a separate,
        # separately-authorised step — a writer that can also write files is a writer that
        # can overwrite one.
        tools=[],
        agent_type="specialist",
        supported_input=["metrics", "text", "rows"],
        supported_output=["document", "markdown"],
        output_schema={
            "document": "string",
            "sections": "list",
            "sources": "list",
            "grounded": "bool",
        },
        permission_ceiling=PermissionLevel.READ,
        default_budget=Budget(seconds=90, tool_calls=0, usd=0.03),
        model_tier=ModelTier.STANDARD,
        cost_profile="moderate",
        latency_profile="moderate",
        privacy_profile="local_preferred",
        system_prompt=(
            "You write short, plain reports from figures you are given. Every number in "
            "your report must appear in the figures provided. Do not compute anything, do "
            "not estimate, and do not add a figure to make a sentence read better. Where a "
            "figure is missing, say it is missing."
        ),
    )

    async def execute(self, contract: JobContract, ctx: Any) -> AgentResult:
        material = self._material(contract)
        if not material:
            return AgentResult(
                agent=self.spec.name,
                ok=False,
                output={"document": "", "sections": [], "sources": []},
                error=(
                    "no analysis reached this step — a report without its figures still "
                    "looks like a report, so I am not writing one"
                ),
                summary="nothing to report on",
            )

        sources = sorted(material)
        figures = self._figures(material)
        body = await self._write(contract, ctx, material, figures)
        sections = self._sections(body)

        return AgentResult(
            agent=self.spec.name,
            ok=True,
            output={
                "document": body,
                "sections": sections,
                # Which step each part rests on. "Where did this number come from" is the
                # first question anyone asks of a report they are about to act on.
                "sources": sources,
                "figures": figures,
                # Whether the prose actually carries the figures it claims to report. See
                # `_is_grounded` — this is the field the Supervisor checks, because
                # "the document is not empty" is satisfied by any string at all.
                "grounded": grounded(body, figures),
            },
            summary=f"wrote a {len(sections)}-section report from {len(sources)} upstream step(s)",
            evidence=[{"sources": sources, "figures": sorted(figures)}],
        )

    # ------------------------------------------------------------------ internals

    def _material(self, contract: JobContract) -> dict[str, dict[str, Any]]:
        """Upstream outputs worth writing about — the empty ones are not material."""
        return {name: output for name, output in contract.upstream.items() if output}

    def _figures(self, material: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """The numbers, lifted out so they can be shown to the model as fixed text.

        Only the keys a computing step actually produces. Sweeping up every scalar in every
        upstream output would put row identifiers and file sizes in front of the model as
        though they were findings.
        """
        figures: dict[str, Any] = {}
        for name, output in material.items():
            for key in ("count", "excluded", "passed", "failed", "pass_mark", "percentages"):
                if key in output:
                    figures[f"{name}.{key}"] = output[key]
            if isinstance(output.get("metrics"), dict):
                figures[f"{name}.metrics"] = output["metrics"]
        return figures

    async def _write(
        self,
        contract: JobContract,
        ctx: Any,
        material: dict[str, dict[str, Any]],
        figures: dict[str, Any],
    ) -> str:
        summaries = {
            name: output.get("summary")
            for name, output in material.items()
            if output.get("summary")
        }
        response = await ctx.think(
            LLMRequest(
                messages=[
                    LLMMessage(role="system", content=self.spec.system_prompt),
                    LLMMessage(
                        role="user",
                        content=(
                            f"Report title: {_title_of(contract)}\n"
                            f"This step: {contract.objective}\n\n"
                            f"Figures (these are correct and complete):\n"
                            f"{json.dumps(figures, ensure_ascii=False, indent=2)}\n\n"
                            f"What each step reported:\n"
                            f"{json.dumps(summaries, ensure_ascii=False, indent=2)}\n\n"
                            "Write the report in Markdown with a heading per section."
                        ),
                    ),
                ],
                tier=ModelTier.STANDARD,
                sensitivity=DataSensitivity.PRIVATE,
                max_tokens=1200,
            )
        )
        text = response.text.strip()
        if not grounded(text, figures):
            # The model returned *something* and it is not a report of this analysis — an
            # offline apology, a refusal, prose about a different question. Passing it on
            # would hand the owner a document that reads like a report, passes "the document
            # is not empty", and contains none of the numbers that were computed for it.
            # The fallback below is plainer and true by construction, which is the trade
            # worth making for an artefact someone is going to act on and forward.
            return self._fallback(contract, figures, summaries)
        return text

    def _fallback(
        self, contract: JobContract, figures: dict[str, Any], summaries: dict[str, Any]
    ) -> str:
        """A report built from the figures alone, when no model was available.

        Plain and short, and *true* — which is the property that matters. An offline
        Thursday that returns nothing here has done all the analysis and thrown it away.
        """
        lines = [f"# {_title_of(contract)}", ""]
        if summaries:
            lines += ["## Summary", ""]
            lines += [f"- {step}: {text}" for step, text in summaries.items()]
            lines.append("")
        if figures:
            lines += ["## Figures", ""]
            for key, value in figures.items():
                rendered = (
                    json.dumps(value, ensure_ascii=False)
                    if isinstance(value, dict | list)
                    else value
                )
                lines.append(f"- **{key}**: {rendered}")
        return "\n".join(lines).strip()

    def _sections(self, body: str) -> list[str]:
        return [line.lstrip("# ").strip() for line in body.splitlines() if line.startswith("#")]
