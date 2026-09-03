"""Vision Agent (§15, V9).

Makes what V6 built reachable from a plan. `VisionService` could already look, identify and
read a screen; nothing could put "look at this" in the middle of a DAG, so a workflow that
needed to see something had to stop and ask a person.

The agent adds no perception of its own and deliberately holds no camera. It calls the
service, and the service is where the camera consent lives (ADR 0020) — every rule about
when a camera may open, for how long, and with the indicator lit, applies unchanged whether
the request came from the owner speaking or from a step in a plan. An agent that could open
a camera itself would be a second door into the one part of this system that most needs
exactly one.

What it contributes is *honesty about uncertainty*. `SceneReading.uncertain` is a third
outcome, distinct from success and failure, and it survives into the agent's result rather
than being flattened into a confident answer or an error. A plan step that says "I looked
and I am not sure" is usable; one that guesses is not.
"""

from __future__ import annotations

from typing import Any

from thursday_shared.enums import ModelTier, PermissionLevel
from thursday_shared.models import AgentResult, AgentSpec, Budget, JobContract

from thursday_agents.base import BaseAgent


class VisionAgent(BaseAgent):
    spec = AgentSpec(
        name="vision",
        description="Looks at the camera or the screen and reports what is there.",
        capabilities=["vision", "identify", "read_screen", "describe", "ocr"],
        # No tools. Perception goes through `VisionService`, which owns camera consent.
        tools=[],
        agent_type="specialist",
        supported_input=["question"],
        supported_output=["answer", "detections", "text"],
        output_schema={"answer": "string", "uncertain": "bool", "surface": "string"},
        # Reading a screen is OPEN, not READ: it is a capture, and the owner's screen is
        # not a file this agent may take without the same permission any capture needs.
        permission_ceiling=PermissionLevel.OPEN,
        default_budget=Budget(seconds=45, tool_calls=0, usd=0.03),
        model_tier=ModelTier.VISION,
        cost_profile="moderate",
        latency_profile="moderate",
        # Frames are the owner's home and desk. The local detector runs first and only a
        # sampled frame ever travels (ADR 0021).
        privacy_profile="local_preferred",
        system_prompt=(
            "You report what is visible. Say what you can see and what you cannot. "
            "Never describe something you did not observe."
        ),
    )

    def __init__(self, vision: Any) -> None:
        super().__init__()
        self._vision = vision

    async def execute(self, contract: JobContract, ctx: Any) -> AgentResult:
        question = str(contract.inputs.get("question") or contract.objective)
        surface = str(contract.inputs.get("surface") or "camera").lower()

        if surface == "screen":
            reading = await self._vision.read_screen(question=question)
            answer = reading.summary or "I could not make out anything on the screen."
            uncertain = reading.uncertain
        else:
            looked = await self._vision.identify(question)
            if looked.refused is not None:
                # No camera grant, or one that expired. A refusal is not a poor answer and
                # must not be dressed as one: a plan step that reports "I looked and saw
                # nothing" when the camera never opened is a step that will be believed.
                return AgentResult(
                    agent=self.spec.name,
                    ok=False,
                    output={"answer": "", "uncertain": True, "surface": surface},
                    error=looked.refused,
                    summary="the camera was not opened",
                )
            answer, uncertain, reading = looked.text, looked.uncertain, looked.reading

        detections = [d.describe() for d in reading.detections] if reading else []
        text = reading.all_text() if reading else ""

        return AgentResult(
            agent=self.spec.name,
            ok=True,
            output={
                "answer": answer,
                # Preserved, not flattened. "I looked and I am not sure" is a usable answer
                # and a different one from either success or failure.
                "uncertain": bool(uncertain),
                "surface": surface,
                "detections": detections,
                "text": text,
            },
            summary=answer[:160],
            evidence=[{"surface": surface, "detections": detections}],
        )
