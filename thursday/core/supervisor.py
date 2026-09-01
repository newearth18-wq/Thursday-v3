"""Supervisor (§18, §76).

Agent output is never trusted because an agent produced it. Verification runs in a fixed
order and stops early: deterministic checks first (schema, completeness, arithmetic,
verification flags, provenance), and only then — if anything remains genuinely uncertain —
an LLM critique. Most verifications therefore cost nothing.

The Supervisor is read-only by construction. A verifier that can edit the work is not a
verifier.
"""

from __future__ import annotations

import re
from typing import Any

from thursday.core.logging import get_logger
from thursday.shared.enums import AgentVerdict, ModelTier
from thursday.shared.models import (
    AgentResult,
    JobContract,
    LLMMessage,
    LLMRequest,
    VerificationReport,
)

log = get_logger(__name__)

_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


class Supervisor:
    def __init__(self, models: object | None = None, *, use_llm_critique: bool = True) -> None:
        self._models = models
        self._use_llm = use_llm_critique

    async def verify(
        self, contract: JobContract, result: AgentResult, *, attempt: int = 1, max_attempts: int = 2
    ) -> VerificationReport:
        checks: list[dict[str, Any]] = []

        checks.append(self._check_ran(result))
        checks.append(self._check_schema(contract, result))
        checks.append(self._check_verification_flags(result))
        checks.append(self._check_provenance(contract, result))
        checks.extend(self._check_arithmetic(result))
        checks.extend(self._check_criteria(contract, result))

        failures = [c for c in checks if not c["ok"]]
        blocking = [c for c in failures if c.get("blocking", True)]

        if not blocking:
            if self._needs_judgement(contract, result, checks):
                critique_check = await self._llm_critique(contract, result)
                checks.append(critique_check)
                if not critique_check["ok"]:
                    blocking = [critique_check]

        if not blocking:
            return VerificationReport(
                verdict=AgentVerdict.PASS,
                checks=checks,
                confidence=self._confidence(checks),
            )

        critique = "; ".join(f"{c['name']}: {c['detail']}" for c in blocking)
        # A failure that more effort could plausibly fix is a RETRY; one that needs a
        # decision, a permission, or a human is an ESCALATE.
        recoverable = all(c.get("recoverable", True) for c in blocking) and attempt < max_attempts
        verdict = AgentVerdict.RETRY if recoverable else AgentVerdict.ESCALATE
        log.info("supervisor_verdict", agent=result.agent, verdict=verdict, failures=len(blocking))
        return VerificationReport(
            verdict=verdict, checks=checks, critique=critique, confidence=self._confidence(checks)
        )

    # ------------------------------------------------------------------ checks

    def _check_ran(self, result: AgentResult) -> dict[str, Any]:
        return {
            "name": "completed",
            "ok": result.ok,
            "detail": result.error or "agent reported success",
            "recoverable": True,
        }

    def _check_schema(self, contract: JobContract, result: AgentResult) -> dict[str, Any]:
        required = [k for k in contract.output_schema if not k.endswith("?")]
        missing = [k for k in required if k not in result.output]
        return {
            "name": "output_schema",
            "ok": not missing,
            "detail": f"missing fields: {', '.join(missing)}" if missing else "all required fields present",
            "recoverable": True,
        }

    def _check_verification_flags(self, result: AgentResult) -> dict[str, Any]:
        """§20 — a tool that ran but could not confirm its effect is not a success."""
        unverified = [t.tool for t in result.tool_results if t.ok and not t.verified]
        return {
            "name": "effects_verified",
            "ok": not unverified,
            "detail": (
                f"these actions could not be confirmed: {', '.join(unverified)}"
                if unverified
                else "every action's effect was observed"
            ),
            # Nothing an agent can do about an unobservable effect; a human decides.
            "recoverable": False,
        }

    def _check_provenance(self, contract: JobContract, result: AgentResult) -> dict[str, Any]:
        """§74 — a research-style answer without sources is not verifiable."""
        wants_sources = any(
            "source" in c.lower() or "cite" in c.lower() or "provenance" in c.lower()
            for c in contract.success_criteria
        )
        if not wants_sources:
            return {"name": "provenance", "ok": True, "detail": "not required", "blocking": False}
        sources = result.output.get("sources") or [e.get("source") for e in result.evidence]
        present = bool([s for s in sources if s])
        return {
            "name": "provenance",
            "ok": present,
            "detail": "sources attached" if present else "the answer carries no sources",
            "recoverable": True,
        }

    def _check_arithmetic(self, result: AgentResult) -> list[dict[str, Any]]:
        """Cheap numeric sanity: percentages, declared totals, and counts."""
        checks: list[dict[str, Any]] = []
        output = result.output

        percentages = output.get("percentages")
        if isinstance(percentages, list) and percentages:
            total = sum(float(p) for p in percentages if isinstance(p, int | float))
            ok = 99.0 <= total <= 101.0
            checks.append({
                "name": "percentages_total",
                "ok": ok,
                "detail": f"percentages sum to {total:.2f}",
                "recoverable": True,
            })

        for count_key in ("count", "total", "processed"):
            declared = output.get(count_key)
            items = output.get("items") or output.get("rows") or output.get("records")
            if isinstance(declared, int) and isinstance(items, list):
                checks.append({
                    "name": f"{count_key}_matches_items",
                    "ok": declared == len(items),
                    "detail": f"declared {declared}, found {len(items)}",
                    "recoverable": True,
                })
        return checks

    def _check_criteria(self, contract: JobContract, result: AgentResult) -> list[dict[str, Any]]:
        """Criteria expressible as a machine check are evaluated here, not by a model."""
        checks: list[dict[str, Any]] = []
        for criterion in contract.success_criteria:
            lowered = criterion.lower()
            if match := re.match(r"output\.(\w+)\s+is\s+(true|false)", lowered):
                field, expected = match.group(1), match.group(2) == "true"
                actual = bool(result.output.get(field))
                checks.append({
                    "name": f"criterion:{criterion}",
                    "ok": actual == expected,
                    "detail": f"output.{field} = {actual}",
                    "recoverable": True,
                })
            elif match := re.match(r"output\.(\w+)\s+is\s+not\s+empty", lowered):
                field = match.group(1)
                value = result.output.get(field)
                checks.append({
                    "name": f"criterion:{criterion}",
                    "ok": bool(value),
                    "detail": f"output.{field} is {'set' if value else 'empty'}",
                    "recoverable": True,
                })
        return checks

    def _needs_judgement(
        self, contract: JobContract, result: AgentResult, checks: list[dict[str, Any]]
    ) -> bool:
        """Only spend a model call when the deterministic checks cannot settle it."""
        if not self._use_llm or self._models is None:
            return False
        machine_checked = {c["name"].removeprefix("criterion:") for c in checks}
        unchecked = [c for c in contract.success_criteria if c not in machine_checked]
        return bool(unchecked) and bool(result.output)

    async def _llm_critique(self, contract: JobContract, result: AgentResult) -> dict[str, Any]:
        request = LLMRequest(
            messages=[
                LLMMessage(
                    role="system",
                    content=(
                        "You verify work products. Judge only whether the output satisfies the "
                        "stated criteria. Do not improve the work. Reply with JSON: "
                        '{"verdict": "PASS"|"RETRY"|"ESCALATE", "critique": "...", '
                        '"confidence": 0.0-1.0}'
                    ),
                ),
                LLMMessage(
                    role="user",
                    content=(
                        f"Objective: {contract.objective}\n"
                        f"Success criteria:\n"
                        + "\n".join(f"- {c}" for c in contract.success_criteria)
                        + f"\n\nAgent output:\n{result.output}\n\nSummary: {result.summary}"
                    ),
                ),
            ],
            tier=ModelTier.REASONING,
            json_schema={"title": "Verification"},
            max_tokens=400,
        )
        response, _ = await self._models.complete(request)  # type: ignore[attr-defined]
        payload = response.structured or {}
        verdict = str(payload.get("verdict", "ESCALATE")).upper()
        return {
            "name": "llm_critique",
            "ok": verdict == "PASS",
            "detail": str(payload.get("critique", "no critique returned")),
            "recoverable": verdict == "RETRY",
        }

    def _confidence(self, checks: list[dict[str, Any]]) -> float:
        if not checks:
            return 0.5
        passed = sum(1 for c in checks if c["ok"])
        return round(passed / len(checks), 2)
