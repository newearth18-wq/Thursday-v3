"""Practice Mode: rehearsing a dangerous action without performing one (§23, §24).

The spec asks for somewhere the owner can be shown what deleting a file, sending an email or
setting up an automation *looks* like, without any of it happening. That is a good thing to
want and a dangerous thing to build, because there are two ways to implement it and they look
almost identical from the outside:

    1.  A flag the executor checks.   "if practice: don't really do it"
    2.  A description that is never executed at all.

**This module is the second, and the difference is the whole point.** A flag means the real
execution path runs with a `practice=True` in its hand, one missed branch away from sending
the email; it means the Permission Engine is asked about an action nobody intends to take,
teaching it that `email.send` was approved; and it means somebody, one day, will pass the flag
from a place that should not have it. That is not a sandbox — it is a bypass with a
reassuring name.

So there is **no execution path here at all**. `rehearse()` builds a *description* of what
would happen from the policy table, and returns it. Nothing is dispatched, no tool is
selected, no `ActionRequest` is constructed, no approval is created. The safety property is
not "the executor was careful"; it is that this module cannot reach the executor, and a test
walks its imports to say so.

The second half is §25 and §26. Rehearsing is also the honest moment to explain *why* an
action asks — a person meets "ส่งอีเมลนี้ไหม?" for the first time in the middle of doing
something else, which is the worst moment to learn what the permission model is. Here they
can meet it with nothing at stake, and the explanation comes from the same policy table that
will decide the real one, so it cannot describe a rule that is not in force.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from thursday_shared.enums import PolicyDecision, RiskLevel

from thursday_core.logging import get_logger

log = get_logger(__name__)


#: Why an action asks, in the owner's language, keyed on the decision the table gives. §25's
#: requirement: the first approval prompt should teach, not just interrupt.
_WHY_IT_ASKS: dict[PolicyDecision, str] = {
    PolicyDecision.ASK_ALWAYS: (
        "งานแบบนี้ผมจะถามคุณก่อนทุกครั้ง เพราะมันมีผลออกไปนอกเครื่อง "
        "หรือย้อนกลับไม่ได้ — และคุณเปลี่ยนกฎนี้ได้ทีหลังในหน้าการอนุญาต"
    ),
    PolicyDecision.ASK_ONCE: ("ครั้งแรกผมจะถามก่อน ถ้าคุณอนุญาต ครั้งต่อ ๆ ไปในเรื่องเดียวกันผมทำให้เลยได้"),
    PolicyDecision.AUTO: "งานแบบนี้ผมทำให้ได้เลย เพราะย้อนกลับได้และไม่ส่งอะไรออกนอกเครื่อง",
    PolicyDecision.BLOCK: "งานแบบนี้ผมทำไม่ได้เลยครับ ไม่ว่าใครสั่ง",
}

#: What each risk level means to somebody who does not think in risk levels.
_RISK_WORDS: dict[RiskLevel, str] = {
    RiskLevel.LOW: "ผลกระทบน้อย",
    RiskLevel.MEDIUM: "มีผลกับงานของคุณ",
    RiskLevel.HIGH: "มีผลมาก ควรอ่านก่อนกด",
    RiskLevel.CRITICAL: "ย้อนกลับไม่ได้",
}


@dataclass(frozen=True)
class Rehearsal:
    """What *would* happen, described. Nothing here was done.

    `happened` exists and is always False. It is not a placeholder for a future mode in which
    practice performs things — it is there so a caller reading this object cannot mistake it
    for a result, and so a test can assert the invariant rather than trust the docstring.
    """

    action: str
    #: What Thursday would do, in the owner's words.
    would: str
    #: What the Permission Engine would decide, read from the live table.
    decision: PolicyDecision
    #: Why it decides that, for a person (§25).
    why: str
    risk: str = ""
    reversible: bool = True
    #: What the owner would be shown, if it would ask.
    prompt: str = ""
    happened: bool = False

    def render(self) -> dict:
        return {
            "practice": True,
            "action": self.action,
            "would": self.would,
            "decision": self.decision.value,
            "why": self.why,
            "risk": self.risk,
            "reversible": self.reversible,
            "prompt": self.prompt,
            # Stated in every payload rather than implied by the endpoint. A client that
            # renders this must have no way to think something occurred.
            "happened": False,
        }


#: What the actions worth rehearsing would do, in the owner's language. Declared rather than
#: generated from the action name: "file.delete" rendered as "delete file" is not a sentence
#: anybody wanted to read, and Sprint 65's rule is that an internal never stands in for one.
_WOULD: dict[str, str] = {
    "file.delete": "ย้ายไฟล์ที่เลือกไปถังขยะ",
    "email.send": "ส่งอีเมลฉบับนี้ออกไปจริง",
    "message.send": "ส่งข้อความนี้ออกไปจริง",
    "automation.create": "ตั้งให้งานนี้ทำเองตามเวลาที่กำหนด",
    "system.restore": "แทนที่ข้อมูลทั้งหมดของ Thursday ด้วยไฟล์สำรอง",
    "system.update": "ติดตั้ง Thursday รุ่นใหม่ทับรุ่นปัจจุบัน",
    "device.wake": "ปลุกเครื่องที่ปิดอยู่ให้เปิดขึ้นมา",
    "app.open": "เปิดโปรแกรมบนเครื่องนี้",
    "file.read": "อ่านไฟล์ที่คุณระบุ",
}

#: The ones the Learning Center offers to rehearse. Everything in `_WOULD` can be rehearsed;
#: these are the ones worth *offering*, because meeting them for the first time in the middle
#: of real work is the bad way to learn what the permission model does.
OFFERED: tuple[str, ...] = ("file.delete", "email.send", "automation.create")


def rehearse(container: Any, action: str, *, resource: str = "") -> Rehearsal:
    """Describe what `action` would do here, and what Thursday would ask.

    Reads the same policy table the real path reads, so the explanation cannot describe a
    rule that is not in force. Builds no `ActionRequest`, calls no engine, dispatches
    nothing: the policy is *looked up*, not *exercised*, so this leaves no trace on approval
    state and cannot teach the engine that anything was allowed.
    """
    table = container.permissions.policy
    autonomy = container.permissions.autonomy
    policy = table.get(action, autonomy=autonomy)

    would = _WOULD.get(action, "")
    if not would:
        # An action nobody has written a sentence for is not rehearsed with a generated one.
        would = "งานนี้ผมยังอธิบายเป็นภาษาคนไม่ได้ครับ"

    if resource:
        would = f"{would} ({resource})"

    decision = policy.default
    rehearsal = Rehearsal(
        action=action,
        would=would,
        decision=decision,
        why=_WHY_IT_ASKS.get(decision, ""),
        risk=_RISK_WORDS.get(policy.risk, ""),
        reversible=policy.reversible,
        prompt=_prompt_for(would, decision),
    )
    log.info("practice_rehearsal", action=action, decision=decision.value)
    return rehearsal


def _prompt_for(would: str, decision: PolicyDecision) -> str:
    """The words the owner would actually see, so the rehearsal shows the real thing."""
    if decision is PolicyDecision.AUTO:
        return ""
    if decision is PolicyDecision.BLOCK:
        return ""
    return f"ผมกำลังจะ{would} — ให้ทำเลยไหมครับ?"


def offers(container: Any) -> list[dict]:
    """The rehearsals worth offering here, each with what it would do."""
    return [rehearse(container, action).render() for action in OFFERED]


def explain_decision(container: Any, action: str) -> str:
    """§35's "ทำไมเมื่อกี้ถึงถามฉันก่อน", answered from the table rather than from memory.

    The alternative — Thursday recalling why it asked — is a sentence generated after the
    fact about a decision it no longer has in front of it. Reading the rule back is both
    cheaper and true.
    """
    table = container.permissions.policy
    policy = table.get(action, autonomy=container.permissions.autonomy)
    why = _WHY_IT_ASKS.get(policy.default, "")
    what = _WOULD.get(action, "")
    if what:
        return f"เพราะงานนั้นคือการ{what} — {why}"
    return why
