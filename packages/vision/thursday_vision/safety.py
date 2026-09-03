"""What a gesture may not do on its own (§29, V7).

A thumbs-up is a hand shape. Hand shapes are misread — by bad light, by a hand at an angle,
by someone gesturing at a person in the room while Thursday happens to be watching. The
recogniser is good, not certain, and the gap between those two words is where this file
lives.

So: **a gesture alone never confirms anything consequential.** Not a deletion, not a
payment, not admin work, not an outward-facing message, not a security change. For those,
a thumbs-up moves the request to the approval flow and a person answers it in words.

This is not a limit on the recogniser's accuracy. Even a perfect recogniser would need it,
because the failure it prevents is not misclassification — it is a gesture that was
correctly recognised and never meant as an instruction at all.
"""

from __future__ import annotations

from dataclasses import dataclass

from thursday_core.logging import get_logger
from thursday_shared.actions import canonical, prefixes
from thursday_shared.enums import PermissionLevel, RiskLevel, risk_at_least

from thursday_vision.gestures import Gesture

log = get_logger(__name__)

#: Namespaces a gesture may never confirm. Prefix-matched, like the action policy (ADR
#: 0007), so a new verb under one of these is covered without anyone remembering to add it.
NEVER_BY_GESTURE: frozenset[str] = frozenset(
    {
        "file.delete",
        "file.move",
        "system",
        "shell",
        "powershell",
        "security",
        "audit",
        "credential",
        "vault",
        "permission",
        "approval",
        "payment",
        "purchase",
        "email.send",
        "message.send",
        "social",
        "browser.submit",
        "app.install",
        "disk",
    }
)

#: Gestures that would otherwise read as an answer to a question.
CONFIRMING_GESTURES: frozenset[Gesture] = frozenset({Gesture.THUMBS_UP, Gesture.OPEN_PALM})

#: Below this, even a harmless gesture command is treated as noise. A recogniser that is
#: half sure is a recogniser that is guessing.
MIN_COMMAND_CONFIDENCE = 0.6


@dataclass(frozen=True)
class GestureVerdict:
    allowed: bool
    reason: str
    #: Set when the gesture was understood but must be answered in words instead.
    needs_words: bool = False

    def __bool__(self) -> bool:
        return self.allowed


def is_consequential(action: str) -> bool:
    """Whether an action is one a gesture must never confirm."""
    name = canonical(action)
    return any(prefix in NEVER_BY_GESTURE for prefix in prefixes(name))


def may_confirm(
    gesture: Gesture,
    *,
    action: str = "",
    confidence: float = 1.0,
    level: PermissionLevel = PermissionLevel.READ,
    risk: RiskLevel = RiskLevel.LOW,
    reversible: bool = True,
) -> GestureVerdict:
    """Can this gesture stand as the answer to this action?

    Four ways to refuse, and each is a different failure being prevented:

    * low confidence — the recogniser is guessing;
    * a named consequential action — the blast radius is too large for a hand shape;
    * high permission or risk — same reasoning, reached from the action's own properties
      rather than from its name, so an action nobody listed is still covered;
    * irreversible — "undo" is what makes a misread gesture survivable, and without it the
      mistake is permanent.
    """
    if confidence < MIN_COMMAND_CONFIDENCE:
        return GestureVerdict(False, f"gesture confidence {confidence:.0%} is too low to act on")

    if gesture not in CONFIRMING_GESTURES:
        return GestureVerdict(False, f"{gesture} is not a confirmation")

    if action and is_consequential(action):
        return GestureVerdict(
            False,
            f"{action} is not something a gesture can confirm — please say so in words",
            needs_words=True,
        )

    if level >= PermissionLevel.EXTERNAL or risk_at_least(risk, RiskLevel.HIGH):
        return GestureVerdict(
            False,
            "this reaches outside the machine; it needs a spoken or typed confirmation",
            needs_words=True,
        )

    if not reversible:
        return GestureVerdict(
            False,
            "this cannot be undone, so it needs more than a gesture",
            needs_words=True,
        )

    return GestureVerdict(True, "a low-risk, reversible action a gesture may confirm")


def check_command(gesture: Gesture, *, confidence: float, action: str = "") -> GestureVerdict:
    """The gate every gesture command passes through before it becomes an action.

    Navigation and pointing are always fine — a swipe that goes the wrong way costs one
    swipe back. Confirmations go through :func:`may_confirm`.
    """
    if confidence < MIN_COMMAND_CONFIDENCE:
        return GestureVerdict(False, f"gesture confidence {confidence:.0%} is too low to act on")

    if gesture in CONFIRMING_GESTURES or gesture is Gesture.THUMBS_DOWN:
        # A cancel is safe to accept on a gesture: refusing to act is the safe direction,
        # and making "stop" harder than "go" would be exactly backwards.
        if gesture is Gesture.THUMBS_DOWN:
            return GestureVerdict(True, "cancelling is always allowed")
        return may_confirm(gesture, action=action, confidence=confidence)

    if action and is_consequential(action):
        return GestureVerdict(
            False,
            f"{action} is not something a gesture can trigger — please say so in words",
            needs_words=True,
        )

    return GestureVerdict(True, "navigation and pointing carry no risk")
