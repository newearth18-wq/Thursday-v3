"""Response Composer (§75, §76).

Turns an execution outcome into what the owner hears. The rule the whole system exists to
uphold lives here: Thursday does not say a thing succeeded unless verification passed, and
when it cannot verify, it says exactly that.
"""

from __future__ import annotations

from thursday.core.persona import detect_language, phrase
from thursday.shared.enums import AvatarState, VoiceMode
from thursday.shared.models import (
    AgentResult,
    ApprovalRequest,
    Citation,
    Intent,
    ThursdayReply,
    VerificationReport,
)


class ResponseComposer:
    def __init__(self, *, quiet_when_others_present: bool = True) -> None:
        self._quiet_when_others = quiet_when_others_present

    # ------------------------------------------------------------------ outcomes

    def acknowledge(self, description: str, *, language: str) -> ThursdayReply:
        return ThursdayReply(
            text=phrase("acknowledge", language, action=description),
            voice_mode=VoiceMode.THINKING,
            avatar_state=AvatarState.WORKING,
        )

    def success(
        self,
        *,
        summary: str,
        verification: VerificationReport,
        language: str,
        intent: Intent | None = None,
        citations: list[Citation] | None = None,
        detail: str | None = None,
        people_present: int = 1,
    ) -> ThursdayReply:
        if not verification.passed:
            # Reaching here with a failed verification is a bug in the caller; degrade to
            # the honest phrasing rather than trusting it.
            return self.unverified(
                summary=summary, verification=verification, language=language, intent=intent
            )
        return ThursdayReply(
            text=phrase("verified_success", language, result=summary),
            voice_mode=self._mode(VoiceMode.SUCCESS, people_present),
            avatar_state=AvatarState.SPEAKING,
            confidence=verification.confidence,
            intent=intent,
            citations=citations or [],
            detail=detail,
            verified=True,
        )

    def unverified(
        self,
        *,
        summary: str,
        verification: VerificationReport,
        language: str,
        intent: Intent | None = None,
        people_present: int = 1,
    ) -> ThursdayReply:
        failures = verification.failures()
        detail = verification.critique or "; ".join(f["detail"] for f in failures)
        return ThursdayReply(
            text=phrase(
                "unverified", language, action=summary, detail=detail or "no evidence returned"
            ),
            voice_mode=self._mode(VoiceMode.WARNING, people_present),
            avatar_state=AvatarState.WARNING,
            confidence=verification.confidence,
            intent=intent,
            detail=detail,
            verified=False,
        )

    def partial_failure(
        self, *, done: str, failed: str, preserved: str, language: str, people_present: int = 1
    ) -> ThursdayReply:
        return ThursdayReply(
            text=phrase("partial_failure", language, done=done, failed=failed, preserved=preserved),
            voice_mode=self._mode(VoiceMode.WARNING, people_present),
            avatar_state=AvatarState.WARNING,
            verified=False,
        )

    def failure(self, *, reason: str, language: str, people_present: int = 1) -> ThursdayReply:
        return ThursdayReply(
            text=phrase("blocked", language, reason=reason),
            voice_mode=self._mode(VoiceMode.WARNING, people_present),
            avatar_state=AvatarState.WARNING,
            verified=False,
        )

    def blocked(self, *, reason: str, language: str) -> ThursdayReply:
        return ThursdayReply(
            text=phrase("blocked", language, reason=reason),
            voice_mode=VoiceMode.URGENT,
            avatar_state=AvatarState.WARNING,
            verified=False,
        )

    def needs_approval(self, approval: ApprovalRequest, *, language: str) -> ThursdayReply:
        header = phrase("needs_approval", language)
        detail = (
            f"action: {approval.action} · device: {approval.device_name or '—'} · "
            f"resource: {approval.resource or '—'}\n"
            f"risk: {approval.risk} · reversible: {'yes' if approval.reversible else 'no'}\n"
            f"expected: {approval.expected_outcome}\n"
            f"if refused: {approval.consequence_of_refusal}"
        )
        if approval.dry_run:
            detail = f"{approval.dry_run.summary()}\n{detail}"
        return ThursdayReply(
            text=f"{header}\n{detail}",
            voice_mode=VoiceMode.URGENT,
            avatar_state=AvatarState.WARNING,
            approvals=[approval],
            detail=detail,
            verified=False,
        )

    def clarify(self, question: str, *, language: str) -> ThursdayReply:
        return ThursdayReply(
            text=phrase("clarify", language, question=question),
            voice_mode=VoiceMode.NORMAL,
            avatar_state=AvatarState.LISTENING,
            confidence=0.4,
        )

    def answer(
        self,
        text: str,
        *,
        language: str,
        confidence: float = 0.8,
        citations: list[Citation] | None = None,
        people_present: int = 1,
    ) -> ThursdayReply:
        return ThursdayReply(
            text=text,
            voice_mode=self._mode(VoiceMode.NORMAL, people_present),
            avatar_state=AvatarState.SPEAKING,
            confidence=confidence,
            citations=citations or [],
        )

    def stopped(self, *, language: str, state: str = "") -> ThursdayReply:
        text = phrase("stopped", language)
        return ThursdayReply(
            text=f"{text} {state}".strip(),
            voice_mode=VoiceMode.NORMAL,
            avatar_state=AvatarState.IDLE,
        )

    # ------------------------------------------------------------------ helpers

    def summarise_agent(self, result: AgentResult, *, language: str) -> str:
        if result.summary:
            return result.summary
        return result.output.get("summary") or ("done" if language == "en" else "ดำเนินการแล้ว")

    def language_of(self, text: str) -> str:
        return detect_language(text)

    def _mode(self, preferred: VoiceMode, people_present: int) -> VoiceMode:
        """§67 — do not read private content aloud with company in the room."""
        if self._quiet_when_others and people_present > 1 and preferred is not VoiceMode.URGENT:
            return VoiceMode.QUIET
        return preferred
