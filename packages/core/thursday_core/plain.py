"""Plain language for the things a person sees (EASY INSTALL) — Sprint 65.

The requirement gives three worked examples and they are all the same move:

    ResearchAgent #84 running tool call 13   →  กำลังค้นข้อมูล
    Vision model inference node GPU-02       →  กำลังวิเคราะห์ภาพ
    SupervisorAgent verifying output         →  กำลังตรวจสอบผลลัพธ์

and one rule with no examples at all, because there is nothing to show:

    Never show raw stack trace to normal users.
    Bad:  ConnectionError ECONNREFUSED localhost:11434
    Good: "AI ภายในเครื่องไม่ตอบสนอง ผมสามารถลองซ่อมให้ได้"

**This is an allowlist, not a filter, and that is the whole design.**

The tempting implementation strips things — remove the agent id, drop the stack trace, hide
the port number. It fails the same way Sprint 49's metrics fallback failed: a filter only
removes what somebody thought of, and the thing nobody thought of is exactly what leaks. It
also fails *invisibly*, because a leaked internal reads as a slightly odd message rather than
as a bug.

So the phrases are **declared in advance**, keyed on something bounded, and anything
unrecognised collapses to a deliberately vague sentence. An activity Thursday has no phrase
for is reported as "กำลังทำงาน" — which is less informative and always true, and the failure
mode is a vague message rather than an agent id in front of somebody who did not ask for one.

Errors work the same way. `friendly()` maps an exception to a sentence and a repair hint from
a declared table; the original text is kept in `technical`, which only Developer Options ever
renders. `str(exc)` never reaches a user-facing field, so no error path has to remember to
sanitise itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

#: What the owner is told while Thursday works, keyed on capability rather than on agent
#: name. Capabilities are a closed vocabulary the agents already declare (V9); agent names
#: are not, and keying on them would mean a new agent silently showing its class name.
ACTIVITY_BY_CAPABILITY: dict[str, str] = {
    "research": "กำลังค้นข้อมูล",
    "search": "กำลังค้นข้อมูล",
    "browse": "กำลังเปิดดูเว็บ",
    "vision": "กำลังวิเคราะห์ภาพ",
    "ocr": "กำลังอ่านข้อความจากภาพ",
    "verify": "กำลังตรวจสอบผลลัพธ์",
    "supervise": "กำลังตรวจสอบผลลัพธ์",
    "plan": "กำลังวางแผน",
    "data": "กำลังคำนวณข้อมูล",
    "document": "กำลังจัดทำเอกสาร",
    "file": "กำลังจัดการไฟล์",
    "code": "กำลังเขียนโค้ด",
    "calendar": "กำลังดูปฏิทิน",
    "message": "กำลังร่างข้อความ",
    "automation": "กำลังตั้งค่าอัตโนมัติ",
    "memory": "กำลังค้นความจำ",
    "device": "กำลังสั่งงานเครื่อง",
}

#: What anything unrecognised becomes. Vague on purpose: it is always true, and it is what
#: keeps an unnamed capability from arriving as an agent id.
WORKING = "กำลังทำงาน"


class Prop(StrEnum):
    """What Thursday is holding while it works (§13 of the avatar addendum) — Sprint 89.

    The addendum's rule is that **Thursday stays the only character**: a sub-agent does not
    become a second robot on the screen, it becomes something the one robot is holding. So
    these are objects, not actors, and there is deliberately no member that stands for "an
    agent" in the abstract.

    Keyed on capability, exactly like `ACTIVITY_BY_CAPABILITY` and for the same reason. The
    tempting shortcut is to key the picture off `activity` — the client already has that
    string — and it is wrong twice: it makes a *drawing* depend on the exact wording of a
    Thai sentence, so rephrasing a caption silently removes a prop; and it gives the client
    a second vocabulary to keep in step with the server. One key, two tables, one choke
    point (§65).

    §13 also names a palette for a design agent and a magnifier alongside the books. There
    is no `design` capability in `ACTIVITY_BY_CAPABILITY`, so there is no palette here: a
    prop nothing can ever be holding is the same claim-without-a-producer this project has
    removed five times. It goes in when the capability does.
    """

    #: Working, with nothing in particular in hand. Every unmapped capability lands here.
    NONE = "NONE"
    #: Research, search, browsing the web.
    BOOKS = "BOOKS"
    #: Numbers.
    CHART = "CHART"
    #: Writing something up.
    PAPERS = "PAPERS"
    #: Code.
    CODE = "CODE"
    #: Looking at an image or reading text out of one.
    SCAN = "SCAN"
    #: Driving another machine.
    SCREEN = "SCREEN"
    #: Checking that work actually did what it claimed (§76, ADR 0012).
    CHECKLIST = "CHECKLIST"


#: Capability → what Thursday holds. Same keys as `ACTIVITY_BY_CAPABILITY` wherever §13
#: names a prop for that kind of work, and deliberately absent where it does not: a
#: capability with no entry draws no prop rather than a vague one, because a picture that
#: means nothing in particular is worse than no picture.
PROP_BY_CAPABILITY: dict[str, Prop] = {
    "research": Prop.BOOKS,
    "search": Prop.BOOKS,
    "browse": Prop.BOOKS,
    "data": Prop.CHART,
    "document": Prop.PAPERS,
    "code": Prop.CODE,
    "vision": Prop.SCAN,
    "ocr": Prop.SCAN,
    "device": Prop.SCREEN,
    "automation": Prop.SCREEN,
    "verify": Prop.CHECKLIST,
    "supervise": Prop.CHECKLIST,
}

#: The words the requirement's own list forbids in front of a normal user, plus the shapes a
#: leaked internal usually takes. Used by `leaks()` and by the test that walks live responses.
FORBIDDEN = (
    "traceback",
    "econnrefused",
    "errno",
    "stacktrace",
    "docker",
    "postgres",
    "postgresql",
    "redis",
    "sqlalchemy",
    "asyncio",
    "ollama",
    "localhost:",
    "127.0.0.1:",
    "http://",
    "https://",
    "none type",
    "nonetype",
    "exception",
    "0x",
)


@dataclass(frozen=True)
class Friendly:
    """An error as a person should meet it."""

    message: str
    #: Whether "Repair Thursday" could plausibly help. The button is only offered when it
    #: could — an unfixable problem with a Repair button beside it teaches people that the
    #: button does nothing.
    repairable: bool = False
    #: The original, for Developer Options and the log. Never rendered by default, and kept
    #: in its own field so no template can include it by accident.
    technical: str = ""


#: Exception type name → what it means to somebody who did not write the code. Keyed on the
#: type rather than on message text: messages change with library versions, types do not.
_BY_TYPE: dict[str, tuple[str, bool]] = {
    "ConnectionError": ("เชื่อมต่อไม่ได้ ผมลองซ่อมให้ได้", True),
    "ConnectionRefusedError": ("บริการที่ต้องใช้ยังไม่ทำงาน ผมลองซ่อมให้ได้", True),
    "TimeoutError": ("ใช้เวลานานเกินไป ลองอีกครั้งได้", True),
    "ModuleNotFoundError": ("ส่วนประกอบบางอย่างหายไป ผมลองซ่อมให้ได้", True),
    "FileNotFoundError": ("หาไฟล์ที่ต้องการไม่พบ", False),
    "PermissionError": ("ผมไม่มีสิทธิ์เข้าถึงสิ่งนั้น", False),
    "OSError": ("เครื่องปฏิเสธคำสั่งนั้น ผมลองซ่อมให้ได้", True),
}

#: Substrings in an exception's text that identify a subsystem, when the type is too generic
#: to say anything. Checked after the type, and each is a *category*, never a passthrough.
_BY_CONTENT: tuple[tuple[str, str, bool], ...] = (
    ("11434", "AI ภายในเครื่องไม่ตอบสนอง ผมลองซ่อมให้ได้", True),
    ("ollama", "AI ภายในเครื่องไม่ตอบสนอง ผมลองซ่อมให้ได้", True),
    ("redis", "ส่วนเก็บสถานะไม่ตอบสนอง ผมลองซ่อมให้ได้", True),
    ("database", "ที่เก็บข้อมูลมีปัญหา ผมลองซ่อมให้ได้", True),
    ("no space left", "พื้นที่ดิสก์เต็ม", False),
)

#: What an unrecognised failure becomes. Says what happened and offers the one useful next
#: step, without pretending to know more than it does.
UNKNOWN_FAILURE = "มีบางอย่างผิดพลาด ผมลองซ่อมให้ได้"


def activity(*, capabilities: Any = (), fallback: str = WORKING) -> str:
    """What to show while an agent with these capabilities works.

    Takes capabilities rather than an agent, so nothing about an agent's identity can reach
    the screen even by accident — there is no parameter for it.
    """
    for capability in capabilities or ():
        head = str(capability).split(".")[0].strip().lower()
        if head in ACTIVITY_BY_CAPABILITY:
            return ACTIVITY_BY_CAPABILITY[head]
    return fallback


def prop(*, capabilities: Any = ()) -> Prop:
    """What Thursday holds while an agent with these capabilities works.

    Takes capabilities and nothing else, the same signature `activity` has and for the same
    reason: there is no parameter an agent's identity could arrive through, so it cannot
    reach the screen even by accident.
    """
    for capability in capabilities or ():
        head = str(capability).split(".")[0].strip().lower()
        if head in PROP_BY_CAPABILITY:
            return PROP_BY_CAPABILITY[head]
    return Prop.NONE


def friendly(exc: BaseException | str) -> Friendly:
    """Turn a failure into something a person can act on.

    A `ThursdayError` already carries a sentence written for a person — a refusal like
    "Pixel is not trusted to control other machines" is exactly what should be shown, and
    wrapping it in a generic apology would lose the one useful thing in it. Everything else
    is mapped, never passed through.
    """
    from thursday_shared.errors import ThursdayError

    if isinstance(exc, ThursdayError):
        return Friendly(message=exc.message, repairable=False, technical=repr(exc))

    text = str(exc)
    if isinstance(exc, BaseException):
        name = type(exc).__name__
        technical = f"{name}: {text}"
        if name in _BY_TYPE:
            # Content still gets a look: a ConnectionError naming port 11434 is more useful
            # as "the local AI is not responding" than as "could not connect".
            specific = _match_content(text)
            message, repairable = specific or _BY_TYPE[name]
            return Friendly(message=message, repairable=repairable, technical=technical)
    else:
        technical = text

    if specific := _match_content(text):
        return Friendly(message=specific[0], repairable=specific[1], technical=technical)
    return Friendly(message=UNKNOWN_FAILURE, repairable=True, technical=technical)


def _match_content(text: str) -> tuple[str, bool] | None:
    lowered = text.lower()
    for needle, message, repairable in _BY_CONTENT:
        if needle in lowered:
            return message, repairable
    return None


def leaks(text: str) -> list[str]:
    """Which forbidden terms a user-facing string contains.

    Exists so the check is one function rather than a regex copied into six tests, and so
    adding a term to `FORBIDDEN` strengthens every check at once.
    """
    lowered = str(text).lower()
    return [term for term in FORBIDDEN if term in lowered]
