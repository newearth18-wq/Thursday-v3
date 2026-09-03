"""Design Agent (§15, V9).

**This agent produces a design specification. It does not produce a picture.**

Stated first because the name invites the other reading. There is no image model here and no
drawing surface, so an agent that promised a mockup would be promising something it fails to
deliver at the point of use.

What it produces instead is the thing that actually precedes a mockup and is more often the
bottleneck: the spec. Structure, a type scale, colour tokens with their roles, the component
list, the states each component needs. That is a real artefact — a person can build from it,
and so can a code generator — and it is the half of design work that survives being written
down.

Two properties make the output worth having rather than decorative:

**Tokens, not adjectives.** "A warm, professional palette" is not a design decision; it is a
description of one that has not been taken yet. The output names concrete values and what
each is *for* — surface, text, accent, danger — because a role is what makes a token usable
and a hex code on its own is not.

**Contrast is checked, not asserted.** A palette that fails legibility is a palette that
looks fine in the spec and unreadable on the screen, and the arithmetic is cheap enough that
there is no excuse for guessing. Pairs that fall below the WCAG AA ratio are reported as
failing rather than quietly shipped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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

#: WCAG AA for body text. 4.5:1 is not a preference; below it, people with ordinary vision
#: in ordinary light stop being able to read the thing.
MIN_CONTRAST = 4.5

#: A starting palette with roles attached. Neutral on purpose — it is a scaffold the owner
#: replaces, not a house style this agent is imposing.
DEFAULT_TOKENS: dict[str, str] = {
    "surface": "#ffffff",
    "surface-raised": "#f4f5f7",
    "text": "#1a1d21",
    "text-muted": "#5c636e",
    "accent": "#2f6feb",
    "danger": "#c62828",
    "border": "#d8dbe0",
}


def _channel(value: float) -> float:
    """One sRGB channel, linearised. The 0.03928 branch is the sRGB transfer curve, not a
    fudge factor — using the raw value instead overstates contrast for dark colours."""
    return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4


def luminance(hex_colour: str) -> float:
    """Relative luminance of a #rrggbb colour, per WCAG."""
    raw = hex_colour.lstrip("#")
    if len(raw) == 3:
        raw = "".join(ch * 2 for ch in raw)
    if len(raw) != 6:
        raise ValueError(f"not a hex colour: {hex_colour!r}")
    r, g, b = (int(raw[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast(foreground: str, background: str) -> float:
    """Contrast ratio between two colours, 1.0–21.0."""
    a, b = luminance(foreground), luminance(background)
    lighter, darker = max(a, b), min(a, b)
    return round((lighter + 0.05) / (darker + 0.05), 2)


@dataclass
class DesignSpec:
    """A design decided but not drawn."""

    intent: str = ""
    tokens: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_TOKENS))
    components: list[str] = field(default_factory=list)
    layout: str = ""
    type_scale: dict[str, str] = field(
        default_factory=lambda: {
            "body": "16px/1.5",
            "heading": "24px/1.25",
            "display": "32px/1.15",
            "caption": "13px/1.4",
        }
    )

    def contrast_report(self) -> list[dict[str, Any]]:
        """Every text-on-surface pair, with its ratio and whether it passes."""
        surfaces = {k: v for k, v in self.tokens.items() if k.startswith("surface")}
        texts = {k: v for k, v in self.tokens.items() if k.startswith("text") or k == "accent"}
        rows: list[dict[str, Any]] = []
        for text_name, text_colour in texts.items():
            for surface_name, surface_colour in surfaces.items():
                try:
                    ratio = contrast(text_colour, surface_colour)
                except ValueError:
                    continue
                rows.append(
                    {
                        "pair": f"{text_name} on {surface_name}",
                        "ratio": ratio,
                        "passes": ratio >= MIN_CONTRAST,
                    }
                )
        return rows


class DesignAgent(BaseAgent):
    spec = AgentSpec(
        name="design",
        description="Produces a design specification — tokens, layout and components. Not an image.",
        capabilities=["design", "layout", "ui", "palette", "specification"],
        tools=[],
        agent_type="specialist",
        supported_input=["intent"],
        supported_output=["spec"],
        output_schema={"spec": "dict", "contrast": "list", "summary": "string"},
        permission_ceiling=PermissionLevel.READ,
        default_budget=Budget(seconds=60, tool_calls=0, usd=0.04),
        model_tier=ModelTier.STANDARD,
        cost_profile="moderate",
        latency_profile="moderate",
        privacy_profile="any",
        system_prompt=(
            "You write design specifications, not descriptions. Name concrete components, "
            "a layout, and what each colour token is for. Never claim to have produced an "
            "image; you are writing the spec somebody builds from."
        ),
    )

    async def execute(self, contract: JobContract, ctx: Any) -> AgentResult:
        intent = str(contract.inputs.get("intent") or contract.objective)
        design = DesignSpec(intent=intent)

        supplied = contract.inputs.get("tokens")
        if isinstance(supplied, dict):
            # The owner's brand wins over the scaffold. Their tokens are still checked —
            # a house palette that fails contrast fails it on their screen too.
            design.tokens.update({str(k): str(v) for k, v in supplied.items()})

        response = await ctx.think(
            LLMRequest(
                messages=[
                    LLMMessage(role="system", content=self.spec.system_prompt),
                    LLMMessage(
                        role="user",
                        content=(
                            f"Design intent: {intent}\n"
                            f"Tokens available: {', '.join(design.tokens)}\n\n"
                            "List the components needed and describe the layout in two or "
                            "three sentences."
                        ),
                    ),
                ],
                tier=ModelTier.STANDARD,
                sensitivity=DataSensitivity.INTERNAL,
                max_tokens=700,
            )
        )
        design.layout = response.text.strip()
        design.components = _components_from(design.layout) or ["header", "content", "footer"]

        report = design.contrast_report()
        failing = [row for row in report if not row["passes"]]
        summary = (
            f"specified {len(design.components)} components and {len(design.tokens)} tokens"
            + (f"; {len(failing)} colour pair(s) fail AA contrast" if failing else "")
        )

        return AgentResult(
            agent=self.spec.name,
            ok=True,
            output={
                "spec": {
                    "intent": design.intent,
                    "tokens": design.tokens,
                    "components": design.components,
                    "layout": design.layout,
                    "type_scale": design.type_scale,
                },
                # Reported whether or not anything failed. A contrast section that only
                # appears when something is wrong is one nobody learns to look for.
                "contrast": report,
                "summary": summary,
                # No image was produced. Said in the output so nothing downstream assumes one.
                "rendered": False,
            },
            summary=summary,
            evidence=[{"components": len(design.components), "failing_pairs": len(failing)}],
        )


def _components_from(text: str) -> list[str]:
    """Component names from a bulleted or numbered reply."""
    names: list[str] = []
    for line in text.splitlines():
        stripped = line.strip().lstrip("-*•0123456789. ").strip()
        if (
            stripped
            and len(stripped) < 60
            and (line.strip().startswith(("-", "*", "•")) or line.strip()[:1].isdigit())
        ):
            names.append(stripped.split(":")[0].strip().lower())
    return names[:12]
