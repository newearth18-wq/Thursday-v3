"""First run (EASY INSTALL, "เมื่อเปิดครั้งแรก") — Sprint 64.

Six steps, and the requirement's target is *"ไม่เกินประมาณ 5–7 user decisions"*. That number
is a design constraint rather than a nicety: every question a normal user cannot answer is a
question that sends them to a forum, and the ones they *can* answer are the ones about
privacy, preference and permission. Anything detectable is detected (Sprint 63) and never
asked.

**The rule this module exists for.**

    "Setup is not considered complete until a real task succeeds."

So `COMPLETE` is not reachable by finishing the last screen. It is reached by Thursday
actually opening Notepad — the command running, the device reporting, and the result coming
back **verified**. Anything less and the wizard stays at `VERIFYING`.

That distinction is the whole point. A wizard that congratulates itself at the end of its own
form has told the owner the assistant works, on no evidence; they close the window believing
it, and find out at the moment they first needed it. The project already refuses to call a
task done without an observation (ADR 0012) — this applies the same rule to the install.

**Resumable, because people close windows.** Each answer is recorded as it is given, so a
first run interrupted at step four resumes at step four rather than at step one. What is not
resumable is the verification: an install that was verified on a machine, and then had its
device node removed, is not still verified, so `restore` brings back the answers and never
the completion.

Nothing here grants a permission. Step four records what the owner *chose* to allow; the
Permission Engine remains the only thing that authorises an action (§95), and a setup answer
is an input to policy, never a substitute for it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from thursday_core.logging import get_logger

log = get_logger(__name__)


class SetupStep(StrEnum):
    """The six screens, in order. `VERIFYING` is the seventh state and not a screen — it is
    where the wizard waits for the machine to prove itself."""

    NAME = "NAME"
    LANGUAGE = "LANGUAGE"
    VOICE = "VOICE"
    PERMISSIONS = "PERMISSIONS"
    AI = "AI"
    TEST_COMMAND = "TEST_COMMAND"
    VERIFYING = "VERIFYING"
    COMPLETE = "COMPLETE"


#: The screens a person actually answers, in order. `VERIFYING` and `COMPLETE` are outcomes.
SCREENS: tuple[SetupStep, ...] = (
    SetupStep.NAME,
    SetupStep.LANGUAGE,
    SetupStep.VOICE,
    SetupStep.PERMISSIONS,
    SetupStep.AI,
    SetupStep.TEST_COMMAND,
)

#: The requirement's own ceiling. Asserted by a test rather than hoped for — every question
#: added later has to displace one, and that is the intended friction.
MAX_DECISIONS = 7


class SetupError(Exception):
    """A step answered out of order, or an answer that is not one."""


@dataclass
class SetupState:
    """Where the first run has got to, and what the owner said."""

    step: SetupStep = SetupStep.NAME
    answers: dict[str, Any] = field(default_factory=dict)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    #: What proved it. Kept because "it worked" is a claim, and the evidence for it is the
    #: thing an owner or a support conversation will want to see.
    proof: dict[str, Any] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return self.step is SetupStep.COMPLETE

    @property
    def decisions(self) -> int:
        """How many questions the owner has actually been asked."""
        return len(self.answers)

    def row(self) -> dict:
        return {
            "step": str(self.step),
            "answers": dict(self.answers),
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "proof": dict(self.proof),
        }


class SetupWizard:
    """Drives the first run and refuses to call it finished on its own say-so."""

    def __init__(self, *, state: SetupState | None = None) -> None:
        self.state = state or SetupState()

    # ------------------------------------------------------------------ the screens

    def answer(self, step: SetupStep, value: Any) -> SetupState:
        """Record one answer and advance.

        Steps are answered in order. Accepting them out of order would let a client skip the
        permissions screen and land on a configured Thursday nobody granted anything to —
        the Permission Engine would still refuse every action, which is safe and baffling.
        """
        if self.state.complete:
            raise SetupError("setup is already finished")
        if step is not self.state.step:
            raise SetupError(f"expected {self.state.step}, got {step}")
        if value is None or (isinstance(value, str) and not value.strip()):
            raise SetupError(f"{step} needs an answer")

        self.state.answers[str(step)] = value
        self.state.step = self._next(step)
        log.info("setup_step_answered", step=str(step), next=str(self.state.step))
        return self.state

    def _next(self, step: SetupStep) -> SetupStep:
        index = SCREENS.index(step)
        if index + 1 < len(SCREENS):
            return SCREENS[index + 1]
        # The last screen leads to waiting for proof, never to COMPLETE.
        return SetupStep.VERIFYING

    # ------------------------------------------------------------------ the proof

    def verify(self, result: Any) -> SetupState:
        """Finish the install, if a real command really worked.

        `result` is whatever the test command produced — a `DeviceActionResult`, a task, or
        anything else carrying `ok` and `verified`. Both are required, and `verified` is the
        one that matters: `ok` says the node did not raise, `verified` says somebody looked
        and Notepad was open (ADR 0012).
        """
        if self.state.step is not SetupStep.VERIFYING:
            raise SetupError(f"nothing to verify yet; setup is at {self.state.step}")

        ok = bool(getattr(result, "ok", False))
        verified = bool(getattr(result, "verified", False))

        if not (ok and verified):
            # Stays at VERIFYING. The owner is told the command did not work and can try
            # again — which is the truth, and is recoverable, unlike a wizard that closed
            # itself and left them believing otherwise.
            log.warning("setup_verification_failed", ok=ok, verified=verified)
            self.state.proof = {
                "ok": ok,
                "verified": verified,
                "error": str(getattr(result, "error", "") or "the command did not complete"),
            }
            return self.state

        self.state.step = SetupStep.COMPLETE
        self.state.completed_at = datetime.now(UTC)
        self.state.proof = {
            "ok": True,
            "verified": True,
            "evidence": dict(getattr(result, "evidence", {}) or {}),
            "at": self.state.completed_at.isoformat(),
        }
        log.info("setup_complete", decisions=self.state.decisions)
        return self.state

    # ------------------------------------------------------------------ what the screen shows

    def progress(self) -> dict:
        """What the wizard renders. Plain sentences; no step numbers the owner must count."""
        return {
            "step": str(self.state.step),
            "complete": self.state.complete,
            "answered": self.state.decisions,
            "remaining": max(0, len(SCREENS) - self.state.decisions),
            "message": _MESSAGES[self.state.step],
            "proof": dict(self.state.proof),
        }

    # ------------------------------------------------------------------ resuming

    @classmethod
    def restore(cls, row: dict) -> SetupWizard:
        """Bring back an interrupted first run — the answers, never the completion.

        An install verified on a machine that has since had its node removed is not still
        verified. Coming back as `VERIFYING` costs the owner one command and is the only
        answer that is true on every restart.
        """
        state = SetupState(
            answers=dict(row.get("answers") or {}),
            started_at=_when(row.get("started_at")),
        )
        step = SetupStep(str(row.get("step") or SetupStep.NAME))
        state.step = SetupStep.VERIFYING if step is SetupStep.COMPLETE else step
        return cls(state=state)


_MESSAGES: dict[SetupStep, str] = {
    SetupStep.NAME: "จะเรียกผมว่าอะไรดี",
    SetupStep.LANGUAGE: "อยากให้ผมใช้ภาษาอะไร",
    SetupStep.VOICE: "เลือกเสียงที่อยากได้",
    SetupStep.PERMISSIONS: "ผมทำอะไรให้ได้บ้าง",
    SetupStep.AI: "ผมตรวจเครื่องแล้ว นี่คือสิ่งที่ผมแนะนำ",
    SetupStep.TEST_COMMAND: "ลองสั่งงานผมสักอย่าง",
    SetupStep.VERIFYING: "กำลังลองทำตามที่สั่ง",
    SetupStep.COMPLETE: "เรียบร้อย ผมพร้อมใช้งานแล้ว",
}


def _when(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if value:
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return datetime.now(UTC)
