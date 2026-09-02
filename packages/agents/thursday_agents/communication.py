"""Communication Agent (§15, V9).

Writes messages. Does not send them.

This is the agent where the system's central rule is least negotiable, so it is worth
stating without hedging: **there is no code path from this agent to a sent message.** It
calls `MessageProvider.draft` and nothing else. `send` exists on the port, is reachable from
the API where a person is on the other end, and is `email.send` / `message.send` in the
policy table — ASK_ALWAYS, never grantable, escalated again when driven from another machine
(ADR 0024).

Every other action in Thursday has an undo or a verification. This one has neither. A file
written wrongly can be restored; an app opened wrongly can be closed; a message sent to the
wrong person is in their inbox, has been read, and no amount of machinery gets it back. The
asymmetry is total, and the design follows it rather than trying to be clever about it.

Two smaller rules follow from the same place:

**No recipient is invented.** A draft with nobody in the `to` field is refused rather than
addressed to a best guess. There is no sensible default recipient for a message.

**Content is what the owner asked for, not what the agent inferred.** The model writes the
prose; it does not decide who needs to know, or add a recipient because the topic mentions
them. A drafting assistant that helpfully cc's a manager has done something nobody can undo.
"""

from __future__ import annotations

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
from thursday_agents.ports import Message, parse_recipients


class CommunicationAgent(BaseAgent):
    spec = AgentSpec(
        name="communication",
        description="Drafts emails and messages for the owner to review and send.",
        capabilities=["communication", "email", "message", "draft", "reply"],
        tools=[],
        agent_type="specialist",
        supported_input=["to", "subject", "intent"],
        supported_output=["draft"],
        output_schema={"draft": "dict", "sent": "bool", "summary": "string"},
        # MODIFY, not EXTERNAL. Writing a draft changes nothing outside this machine; that
        # is the whole point, and the ceiling says so rather than relying on the agent's
        # own restraint.
        permission_ceiling=PermissionLevel.MODIFY,
        default_budget=Budget(seconds=60, tool_calls=0, usd=0.04),
        model_tier=ModelTier.STANDARD,
        cost_profile="moderate",
        latency_profile="moderate",
        privacy_profile="local_preferred",
        system_prompt=(
            "You draft messages for the owner to review. Write only what they asked to "
            "say. Do not add recipients, do not add commitments they did not make, and do "
            "not soften or sharpen their meaning."
        ),
    )

    def __init__(self, messages: Any) -> None:
        super().__init__()
        self._messages = messages

    async def execute(self, contract: JobContract, ctx: Any) -> AgentResult:
        recipients = parse_recipients(contract.inputs.get("to"))
        if not recipients:
            # Refused rather than defaulted. There is no sensible default recipient, and
            # the failure mode of guessing one is a message in a stranger's inbox.
            return AgentResult(
                agent=self.spec.name,
                ok=False,
                output={"draft": {}, "sent": False, "summary": ""},
                error="no recipient was given, and I will not guess who this is for",
                summary="no recipient",
            )

        intent = str(contract.inputs.get("intent") or contract.objective)
        subject = str(contract.inputs.get("subject") or "").strip()
        channel = str(contract.inputs.get("channel") or "email").lower()

        response = await ctx.think(
            LLMRequest(
                messages=[
                    LLMMessage(role="system", content=self.spec.system_prompt),
                    LLMMessage(
                        role="user",
                        content=(
                            f"Channel: {channel}\n"
                            f"To: {', '.join(recipients)}\n"
                            f"Subject: {subject or '(none given)'}\n"
                            f"What the owner wants to say: {intent}\n\n"
                            "Write the message body only."
                        ),
                    ),
                ],
                tier=ModelTier.STANDARD,
                # A message often quotes something private. It never needs to be SECRET to
                # be nobody else's business.
                sensitivity=DataSensitivity.PRIVATE,
                max_tokens=700,
            )
        )
        body = response.text.strip() or intent

        draft = await self._messages.draft(
            Message(
                channel=channel,
                to=recipients,
                subject=subject or _subject_from(intent),
                body=body,
            )
        )

        summary = f"drafted a {channel} to {', '.join(recipients)} — not sent"
        return AgentResult(
            agent=self.spec.name,
            ok=True,
            output={
                "draft": {
                    "id": str(draft.id),
                    "channel": draft.channel,
                    "to": list(draft.to),
                    "subject": draft.subject,
                    "body": draft.body,
                },
                # Always false from this agent. It is in the output rather than merely true
                # by construction, so that anything reading the result can assert on it.
                "sent": False,
                "summary": summary,
            },
            summary=summary,
            evidence=[{"channel": channel, "recipients": len(recipients), "sent": False}],
        )


def _subject_from(intent: str) -> str:
    """A subject line from the request, when none was given.

    The first clause, trimmed — not a generated one. A model-written subject is a second
    thing the owner has to check, and the value of "Re: your question about Thursday" over
    the first eight words of their own sentence is not worth that.
    """
    first = intent.strip().split("\n")[0]
    words = first.split()
    return " ".join(words[:8]) + ("…" if len(words) > 8 else "")
