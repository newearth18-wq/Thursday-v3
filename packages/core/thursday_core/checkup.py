"""Check Thursday, and Repair Thursday (EASY INSTALL) — Sprint 66.

The requirement asks for two buttons and is precise about both.

    Settings → Check Thursday  →  "Everything OK"
                              or  "Local AI ไม่ตอบสนอง — Repair"
    Button   → Repair Thursday →  restart services · repair configuration
                                  · reconnect local AI · repair database · re-register Node
                              but "ห้ามแก้ไข security-sensitive state โดยไม่มี confirmation"

**Neither is a new mechanism.** `Container.health()` already knows whether Thursday can work,
and `SelfRecovery` (§59, V10) already draws the security boundary — its allowlist refuses a
forbidden repair at *registration*, so a repair that changes what Thursday is permitted to do
cannot exist to be called. This module translates the first and drives the second. Building a
second health check or a second repair path would mean two things to keep in agreement, and
the one somebody forgets is the one with the security boundary in it.

**What it adds is language and a next step.** `health()` reports `model:rule-based`, `redis`,
and a
masked database URL — correct, and written for whoever wired it. A person asking "is Thursday
working?" needs "AI ในเครื่องไม่ตอบสนอง" and a button. Names are translated from a declared
table (Sprint 65's rule: an allowlist, so an unrecognised component becomes a vague truth
rather than a leaked internal).

**A problem is only offered Repair if Repair could fix it.** The forbidden repairs are exactly
the ones that would look most helpful in a crisis — re-pair the device, reset the policy — and
a button beside them would be a way around the Permission Engine labelled "fix". So the button
appears when `SelfRecovery` would actually accept the action, and otherwise the owner is told
what needs a person.

**And a repair reports what the machine shows, not what the handler returned.** ACT → VERIFY
(ADR 0012) applies to the subsystem whose whole job is fixing things, and applies hardest
there: the container wires two of the three repairs to placeholders that do nothing, and the
first version of `repair()` ran one, saw no exception, and told the owner it was fixed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from thursday_core.logging import get_logger
from thursday_core.recovery import is_self_repairable

log = get_logger(__name__)

#: Internal component name → what it is called in front of a person, and the repair that would
#: genuinely restore the capability. Declared rather than derived: an unrecognised component
#: becomes "ส่วนประกอบภายใน" with no repair offered, which is vague and true.
#:
#: Two of these eleven carry a repair, and one more comes from the model rows below. That is
#: the honest count: reconnecting a node, restarting a worker and falling back to another model
#: tier are things Thursday can actually do. Starting a database, starting somebody's inference
#: server, or clearing a cache that is not the problem are not — and a button that does nothing
#: teaches the owner that the buttons do nothing, including the ones that work.
COMPONENTS: dict[str, tuple[str, str | None]] = {
    "devices": ("การเชื่อมต่อกับเครื่อง", "reconnect_node"),
    "database": ("ที่เก็บข้อมูล", None),
    "redis": ("ส่วนเก็บสถานะ", None),
    "memory": ("ความจำ", None),
    "audit": ("บันทึกการทำงาน", None),
    "spend": ("ค่าใช้จ่าย", None),
    "approvals": ("การขออนุญาต", None),
    "queue": ("คิวงาน", "restart_worker"),
    "automations": ("งานอัตโนมัติ", None),
    "voice": ("เสียง", None),
    "skills": ("ทักษะที่เรียนรู้", None),
}

#: Model checks arrive as `model:<provider>:<model>`. The provider id is never shown, but it
#: is what decides whether this is the AI on the owner's machine or the one on somebody's
#: server — and the requirement's own example ("Local AI ไม่ตอบสนอง") depends on getting that
#: right. So the runtimes are **declared**, in both directions, and a provider on neither list
#: is described as "AI" with no claim about where it runs.
#:
#: The first version asked whether the string contained "cloud". No provider is called that —
#: they are `rule-based`, `ollama:…`, `anthropic:…` — so every model failure, cloud included,
#: was reported to the owner as their local AI being down.
LOCAL_RUNTIMES: frozenset[str] = frozenset(
    {"rule-based", "rule", "mock", "ollama", "lmstudio", "llamacpp", "llama.cpp", "vllm"}
)
CLOUD_RUNTIMES: frozenset[str] = frozenset(
    {"anthropic", "openai", "azure", "google", "gemini", "bedrock", "mistral", "groq", "together"}
)

#: A model that is not answering is not a model Thursday can restart — starting somebody's
#: inference server is not on the self-repair list and installing one needs approval
#: (ADDENDUM §41). What it *can* do is use a different tier, which is the same move the
#: Model Router makes on its own.
_LOCAL_MODEL = ("AI ในเครื่อง", "switch_model")
_CLOUD_MODEL = ("AI บนคลาวด์", "switch_model")
_SOME_MODEL = ("AI", "switch_model")

UNKNOWN_COMPONENT = "ส่วนประกอบภายใน"

EVERYTHING_OK = "ทุกอย่างปกติ"


@dataclass(frozen=True)
class Finding:
    """One thing that is or is not working, as the owner meets it."""

    component: str
    label: str
    ok: bool
    #: The repair that would plausibly help, or None. Present only when `SelfRecovery` would
    #: actually accept it — a Repair button beside something it cannot fix teaches people
    #: the button does nothing.
    repair: str | None = None
    #: The internal detail, for Developer Options. Never rendered by default — `render()`
    #: gates it, and so does the endpoint that returns a repair's result.
    technical: str = ""

    @property
    def repairable(self) -> bool:
        return bool(self.repair)

    def message(self) -> str:
        return f"{self.label}ปกติ" if self.ok else f"{self.label}ไม่ตอบสนอง"


@dataclass
class Checkup:
    """What "Check Thursday" produces."""

    findings: list[Finding] = field(default_factory=list)
    #: Services somebody has to start, from `Settings.external_services()`. Named rather
    #: than left to a connection error, which is Sprint 62's whole point.
    #:
    #: These are the one place a product name reaches this screen, and it is not a hole in
    #: Sprint 65's rule. On a desktop install the list is **empty by construction** — SQLite
    #: and an in-process cache, so `external_services()` has nothing to return — and a test
    #: below asserts it. It is non-empty only where somebody set `REDIS_URL` or a Postgres
    #: DSN by hand, and that person is exactly the reader who needs the name rather than
    #: "a service is not running".
    missing_services: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(f.ok for f in self.findings) and not self.missing_services

    @property
    def problems(self) -> list[Finding]:
        return [f for f in self.findings if not f.ok]

    def headline(self) -> str:
        """The one line the settings screen shows."""
        if self.ok:
            return EVERYTHING_OK
        if self.missing_services:
            return f"ต้องเปิด {', '.join(self.missing_services)} ก่อน"
        first = self.problems[0]
        return f"{first.label}ไม่ตอบสนอง" + (" — ซ่อมได้" if first.repairable else "")

    def render(self, *, advanced: bool = False) -> dict:
        """The screen. `advanced` is Developer Options and nothing else turns it on.

        `technical` carries whatever `health()` wrote — a masked DSN, a connection error, a
        provider id — and that is the field the requirement is about. It is opt-in rather
        than filtered, because a filter only removes what somebody thought of (Sprint 65).
        """
        problems = []
        for finding in self.problems:
            row: dict[str, Any] = {
                "what": finding.label,
                "message": finding.message(),
                "repair": finding.repair,
            }
            if advanced:
                row["component"] = finding.component
                row["technical"] = finding.technical
            problems.append(row)
        return {
            "ok": self.ok,
            "headline": self.headline(),
            "problems": problems,
            "checked": len(self.findings),
            "missing_services": list(self.missing_services),
        }


def describe(component: str) -> tuple[str, str | None]:
    """Translate one internal component name. Unrecognised becomes vague, never raw.

    One function, called from both `check` and `repair`, so the two screens cannot end up
    calling the same thing by two different names.
    """
    if component.startswith("model:"):
        runtime = component.removeprefix("model:").split(":", 1)[0].strip().lower()
        if runtime in LOCAL_RUNTIMES:
            return _LOCAL_MODEL
        if runtime in CLOUD_RUNTIMES:
            return _CLOUD_MODEL
        # Neither list. "AI ไม่ตอบสนอง" is less useful than naming where it runs, and it is
        # the only one of the three that is certainly true.
        return _SOME_MODEL
    return COMPONENTS.get(component, (UNKNOWN_COMPONENT, None))


async def check(container: Any) -> Checkup:
    """Run the health checks Thursday already has, and say what they mean.

    Reads `Container.health()` rather than re-deriving anything: two health checks would be
    two things to keep in agreement, and the one that drifts is the one nobody is watching.
    """
    result = Checkup(missing_services=list(container.settings.external_services()))

    for raw in await container.health():
        component = str(raw.get("component", ""))
        ok = bool(raw.get("ok", False))
        label, repair = describe(component)

        # Only offer a repair the recovery layer would actually accept. `is_self_repairable`
        # is the same predicate `SelfRecovery.register` uses, so the button and the boundary
        # cannot disagree.
        offered = repair if (repair and not ok and is_self_repairable(repair)) else None
        result.findings.append(
            Finding(
                component=component,
                label=label,
                ok=ok,
                repair=offered,
                technical=str(raw.get("detail", "")),
            )
        )

    log.info("checkup_run", ok=result.ok, problems=len(result.problems))
    return result


#: Why a repair did not happen, in the owner's language. The raw reason from `SelfRecovery`
#: is written for an operator and names the action; it is kept in `technical`.
NEEDS_A_PERSON = "เรื่องนี้ผมทำเองไม่ได้ ต้องให้คนตัดสินใจ"
GAVE_UP = "ผมลองซ่อมหลายครั้งแล้วยังไม่สำเร็จ ต้องให้คนช่วยดู"
NO_REPAIR = "ส่วนนี้ยังไม่มีวิธีซ่อมอัตโนมัติ"


async def repair(container: Any, component: str, action: str) -> dict:
    """Drive one repair, through the layer that owns the boundary, and then check.

    Two things this deliberately does not do.

    It does not decide anything. `SelfRecovery.repair` is the only call here, so a repair
    that changes what Thursday is permitted to do is refused for what it *is*, in the same
    words whether the owner, a model or a persuaded browser asked for it. Anything else —
    a shortcut for an unregistered repair, a "force" flag, a retry that skips the attempt
    budget — would be a second door to the thing V10 built one door for.

    And it does not believe the handler. A repair that returns without raising has proved
    that a function ran, not that anything works: the container currently wires
    `reconnect_node` and `switch_model` to placeholders, and the first version of this
    function answered "ซ่อมเรียบร้อย" to a machine in exactly the state it was in. So the
    component is checked again afterwards and the answer comes from the check — ACT → VERIFY
    (ADR 0012), and §194's rule that nothing is marked done without verification.
    """
    recovery = container.recovery
    label, _ = describe(component)
    if recovery is None:  # pragma: no cover - the container always wires one
        return {
            "attempted": False,
            "ok": False,
            "verified": None,
            "message": NO_REPAIR,
            "needs_a_person": True,
            "technical": "no recovery layer is wired",
        }

    outcome = await recovery.repair(component, action)

    if not outcome.attempted:
        # Three reasons, and the owner should be able to tell them apart: this may never be
        # done automatically, Thursday has run out of attempts, or nothing is wired up.
        if not is_self_repairable(action):
            message = NEEDS_A_PERSON
        elif component in recovery.exhausted():
            message = GAVE_UP
        else:
            message = NO_REPAIR
        return {
            "attempted": False,
            "ok": False,
            "verified": None,
            "message": message,
            # All three mean the same thing for what happens next: somebody has to look.
            "needs_a_person": True,
            "technical": outcome.reason,
        }

    if not outcome.ok:
        return {
            "attempted": True,
            "ok": False,
            "verified": False,
            "message": f"ลองซ่อม{label}แล้วยังไม่สำเร็จ",
            "needs_a_person": False,
            "technical": outcome.reason,
        }

    verified = await _is_healthy_now(container, component)
    if verified is True:
        message = f"ซ่อม{label}เรียบร้อย"
    elif verified is False:
        message = f"ลองซ่อม{label}แล้ว แต่ยังไม่กลับมาทำงาน"
    else:
        # Nothing reports on this component, so there is no observation to derive an answer
        # from. Saying so is the honest outcome; claiming success is the one that costs the
        # owner an afternoon.
        message = f"สั่งซ่อม{label}แล้ว แต่ตรวจสอบผลไม่ได้"

    return {
        "attempted": True,
        # `ok` follows the observation, not the handler. A repair that ran and changed
        # nothing is not a repair that worked.
        "ok": verified is True,
        "verified": verified,
        "message": message,
        "needs_a_person": False,
        "technical": outcome.reason,
    }


async def _is_healthy_now(container: Any, component: str) -> bool | None:
    """Whether this component passes its health check now. None if nothing checks it."""
    for raw in await container.health():
        if str(raw.get("component", "")) == component:
            return bool(raw.get("ok", False))
    return None
