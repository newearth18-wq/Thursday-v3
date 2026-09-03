"""Data Agent (§15, V9).

Turns a table into numbers, and — the part that matters — into numbers the Supervisor can
check. Every figure it reports is computed here from rows it can point at, and the rows come
with it (`output.rows`, `output.count`) so `Supervisor._check_arithmetic` can confirm the
count matches and the percentages add up.

That constraint shapes the whole agent. It does not ask a model what the average is. A model
asked for an average produces a plausible number, and a plausible number that is wrong is
the worst possible output for a report someone is going to act on — it survives every check
except the one nobody ran. Arithmetic is arithmetic; the model is used for the sentence at
the end, over figures that are already fixed.

What it will not do is guess at structure. A file it cannot parse into rows and columns
produces ``ok=False`` with what it found, not an analysis of an empty table.
"""

from __future__ import annotations

import csv
import io
import json
import math
import statistics
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

#: Above this many rows, the model sees a sample and the statistics see everything. The
#: figures are computed from every row regardless — this only bounds what goes into a prompt.
PROMPT_SAMPLE_ROWS = 20

#: Column names that mean "this record should not be counted". Exclusion is not something to
#: infer from a model: "exclude inactive records" is a step someone stated, and a report that
#: quietly included them would be wrong in a way that looks right.
_INACTIVE_MARKERS = frozenset({"inactive", "withdrawn", "dropped", "ลาออก", "พักการเรียน", "no"})
_ACTIVE_COLUMNS = ("active", "status", "enrolled", "สถานะ")


def parse_rows(payload: Any) -> list[dict[str, Any]]:
    """Get rows out of whatever the upstream step produced.

    Deliberately tolerant about the container and strict about the content: a list of dicts,
    a CSV string, or a JSON array all become rows, and anything else becomes no rows at all
    rather than one row holding a blob of text. An agent that invents structure to have
    something to analyse produces confident nonsense.
    """
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("rows", "records", "items", "data"):
            if isinstance(payload.get(key), list):
                return parse_rows(payload[key])
        if isinstance(payload.get("content"), str):
            return parse_rows(payload["content"])
        return []
    if not isinstance(payload, str) or not payload.strip():
        return []

    text = payload.strip()
    if text.startswith("["):
        try:
            return parse_rows(json.loads(text))
        except json.JSONDecodeError:
            return []
    try:
        reader = csv.DictReader(io.StringIO(text))
        rows = [{k: v for k, v in row.items() if k is not None} for row in reader]
    except (csv.Error, UnicodeDecodeError):
        return []
    return rows if len(rows) > 0 and any(rows[0].values()) else []


def is_active(row: dict[str, Any]) -> bool:
    """Whether a record counts. Unknown means *yes* — see below."""
    for column in _ACTIVE_COLUMNS:
        for key, value in row.items():
            if key.strip().lower() != column:
                continue
            if isinstance(value, bool):
                return value
            if str(value).strip().lower() in _INACTIVE_MARKERS:
                return False
    # No status column, or a value nobody recognises. Counting it is the honest default:
    # silently dropping records the agent did not understand would shrink a total with no
    # trace, and a total that is quietly too small reads exactly like a correct one.
    return True


def numeric_columns(rows: list[dict[str, Any]]) -> dict[str, list[float]]:
    """Columns whose values are numbers in most rows.

    "Most" rather than "all" because real exports have a blank cell and a stray "N/A", and
    refusing to analyse a column over one blank would refuse to analyse anything.
    """
    if not rows:
        return {}
    columns: dict[str, list[float]] = {}
    for key in rows[0]:
        values: list[float] = []
        for row in rows:
            raw = row.get(key)
            if isinstance(raw, bool) or raw is None or raw == "":
                continue
            try:
                number = float(raw)
            except (TypeError, ValueError):
                continue
            if math.isfinite(number):
                values.append(number)
        if values and len(values) >= max(1, len(rows) // 2):
            columns[key] = values
    return columns


def describe(values: list[float]) -> dict[str, float]:
    return {
        "count": len(values),
        "mean": round(statistics.fmean(values), 4),
        "median": round(statistics.median(values), 4),
        "min": min(values),
        "max": max(values),
        "stdev": round(statistics.stdev(values), 4) if len(values) > 1 else 0.0,
    }


class DataAgent(BaseAgent):
    spec = AgentSpec(
        name="data",
        description="Computes statistics over tabular data, with the rows attached.",
        capabilities=["data", "analysis", "statistics", "calculate", "aggregate", "chart"],
        # No tools: it works on what the previous step already read. Giving it file access
        # would let it widen the blast radius of an analysis into a second file read that
        # nobody planned or authorised.
        tools=[],
        agent_type="specialist",
        supported_input=["rows", "csv", "json"],
        supported_output=["metrics", "rows", "chart_spec"],
        output_schema={"rows": "list", "count": "int", "metrics": "dict", "summary": "string"},
        permission_ceiling=PermissionLevel.READ,
        default_budget=Budget(seconds=60, tool_calls=0, usd=0.02),
        model_tier=ModelTier.STANDARD,
        cost_profile="cheap",
        latency_profile="fast",
        # Numbers about the owner's data are private, and every figure is computed locally.
        privacy_profile="local_preferred",
        system_prompt=(
            "You describe a table that has already been analysed. The figures are given to "
            "you and are correct; restate them, do not recompute them, and do not add any "
            "number that is not in the figures you were given."
        ),
    )

    async def execute(self, contract: JobContract, ctx: Any) -> AgentResult:
        rows = self._rows_from(contract)
        if not rows:
            return AgentResult(
                agent=self.spec.name,
                ok=False,
                output={"rows": [], "count": 0, "gap": "no tabular data reached this step"},
                error=(
                    "nothing upstream produced rows I could read — "
                    "I will not describe a table I could not parse"
                ),
                summary="no data to analyse",
            )

        included = [row for row in rows if is_active(row)]
        excluded = len(rows) - len(included)
        columns = numeric_columns(included)
        metrics = {name: describe(values) for name, values in columns.items()}

        pass_mark = float(contract.inputs.get("pass_mark", 50))
        distribution = self._distribution(columns, pass_mark)

        summary = await self._narrate(contract, ctx, metrics, distribution, len(included), excluded)

        return AgentResult(
            agent=self.spec.name,
            ok=True,
            output={
                # `rows` and `count` are what the Supervisor cross-checks against, and they
                # are the analysed set, not the input set — a count that did not match what
                # was measured would be worse than no count.
                "rows": included,
                "count": len(included),
                "excluded": excluded,
                "metrics": metrics,
                "summary": summary,
                # Charting is a downstream concern; what this agent owes it is a spec that
                # names real columns rather than an image it cannot verify.
                "chart_spec": self._chart_spec(columns),
                **distribution,
            },
            summary=(
                f"analysed {len(included)} rows across {len(columns)} numeric columns"
                + (f", excluding {excluded} inactive" if excluded else "")
            ),
            evidence=[{"columns": sorted(columns), "excluded": excluded, "rows_seen": len(rows)}],
        )

    # ------------------------------------------------------------------ internals

    def _rows_from(self, contract: JobContract) -> list[dict[str, Any]]:
        """Rows from an upstream step, falling back to the planner's own inputs."""
        for output in contract.upstream.values():
            if rows := parse_rows(output):
                return rows
        return parse_rows(contract.inputs.get("rows") or contract.inputs.get("data"))

    def _distribution(self, columns: dict[str, list[float]], pass_mark: float) -> dict[str, Any]:
        """Pass rate, as a percentage pair that sums to 100.

        Reported as `percentages` because that is the key `Supervisor._check_arithmetic`
        reads — a figure the Supervisor can check is worth more than one it cannot.
        """
        scored = next(
            (
                values
                for name, values in columns.items()
                if any(word in name.lower() for word in ("score", "grade", "mark", "คะแนน"))
            ),
            next(iter(columns.values()), []),
        )
        if not scored:
            return {}
        passed = sum(1 for value in scored if value >= pass_mark)
        rate = 100.0 * passed / len(scored)
        return {
            "pass_mark": pass_mark,
            "passed": passed,
            "failed": len(scored) - passed,
            "percentages": [round(rate, 2), round(100.0 - rate, 2)],
            "percentage_labels": ["passed", "failed"],
        }

    def _chart_spec(self, columns: dict[str, list[float]]) -> dict[str, Any] | None:
        if not columns:
            return None
        name, values = next(iter(columns.items()))
        return {"kind": "histogram", "column": name, "points": len(values)}

    async def _narrate(
        self,
        contract: JobContract,
        ctx: Any,
        metrics: dict[str, Any],
        distribution: dict[str, Any],
        included: int,
        excluded: int,
    ) -> str:
        """One sentence over figures that are already fixed."""
        if not metrics:
            return f"{included} rows, no numeric column to summarise"
        response = await ctx.think(
            LLMRequest(
                messages=[
                    LLMMessage(role="system", content=self.spec.system_prompt),
                    LLMMessage(
                        role="user",
                        content=(
                            f"Objective: {contract.objective}\n"
                            f"Rows analysed: {included} (excluded {excluded} inactive)\n"
                            f"Figures: {json.dumps(metrics, ensure_ascii=False)}\n"
                            f"Distribution: {json.dumps(distribution, ensure_ascii=False)}"
                        ),
                    ),
                ],
                tier=ModelTier.STANDARD,
                sensitivity=DataSensitivity.PRIVATE,
                max_tokens=400,
            )
        )
        text = response.text.strip()
        plain = (
            f"{included} rows analysed across {len(metrics)} numeric columns"
            + (
                f"; {distribution['passed']} at or above {distribution['pass_mark']:g}"
                if distribution
                else ""
            )
            + (f"; {excluded} inactive excluded" if excluded else "")
        )
        # Not `text or plain`. A model that returns an unrelated non-empty string — an
        # offline apology, most often — would pass that test and put a sentence about
        # nothing where the analysis should be. See `thursday_agents.grounding`.
        return text if grounded(text, {"metrics": metrics, **distribution}) else plain
