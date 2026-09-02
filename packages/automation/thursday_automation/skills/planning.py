"""Turning a skill into a plan the orchestrator can run (§50, V9).

A skill is a list of tool calls someone demonstrated. A plan is a DAG of agent steps with
success criteria. Both describe the same work, and this is the one place that converts
between them.

The conversion is not a rename. Three things have to be decided here, and each is a place
where getting it wrong would be invisible:

**Which agent runs each step.** A skill step names a *tool* (`file.read`, `web.search`);
a plan step names an agent. The mapping goes through the tool's namespace, the same
prefix-walk the permission policy uses (ADR 0007), so a skill that learned a verb nobody
has seen before still lands on an agent that can run its namespace.

**How the steps depend on each other.** A demonstration is a sequence — the owner did this,
then that — and a sequence is the only dependency structure that can honestly be recovered
from watching. Two steps that *happened* to be adjacent may or may not be independent, and
guessing that they are, to run them in parallel, would reorder someone's workflow on no
evidence. So the chain is linear, and a skill wanting parallelism has to say so.

**What counts as success.** Inherited from the skill's own declared output schema where it
has one, and otherwise the same floor every other plan step gets: the step ran and, if it
touched a device, its effect was observed.

A skill that is not ACTIVE does not convert. Draft and testing skills exist precisely so
that they can be examined before they run against real data (§52), and a converter that
quietly ran a draft would remove the only thing the lifecycle is for.
"""

from __future__ import annotations

from thursday_shared.actions import canonical, prefixes
from thursday_shared.enums import StepKind
from thursday_shared.models import Plan, PlanStep

from thursday_automation.skills.models import Skill, SkillStatus, SkillVersion

#: Tool namespace → the agent that owns it. Prefix-walked, so `file.folder.create` is served
#: by the `file` entry without an entry of its own.
TOOL_AGENTS: dict[str, str] = {
    "app": "computer",
    "file": "computer",
    "window": "computer",
    "screen": "computer",
    "system": "computer",
    "shell": "computer",
    "powershell": "computer",
    "clipboard": "computer",
    "audio": "computer",
    "notify": "computer",
    "browser": "browser",
    "web": "research",
    "memory": "research",
    "obsidian": "research",
    "data": "data",
    "chart": "data",
    "analyse": "data",
    "document": "document",
    "report": "document",
}

#: Where a *registered* tool maps to no namespace above. Reachable only for a tool that
#: exists but sits in a namespace nobody has mapped: the sandbox refuses to activate a skill
#: naming an unregistered tool, so a typo never gets this far. Every unmapped tool in the
#: catalogue today is a device command, which is what makes `computer` the right default
#: rather than a refusal.
DEFAULT_AGENT = "computer"


def agent_for(tool: str) -> str:
    """Which agent runs this tool, by namespace. Tool steps only — see `_agent_of`."""
    name = canonical(tool)
    for prefix in reversed(prefixes(name)):
        if prefix in TOOL_AGENTS:
            return TOOL_AGENTS[prefix]
    return DEFAULT_AGENT


class SkillNotRunnable(Exception):
    """A skill that exists but may not be run as it stands."""


def plan_from_skill(skill: Skill, *, inputs: dict | None = None) -> Plan:
    """Convert an **active** skill into an executable plan.

    ``inputs`` are merged into each step's arguments *under* the step's own values, so a
    caller can supply what the demonstration left open (a file path, a pass mark) without
    being able to rewrite what the skill actually does. A caller that could overwrite a
    step's arguments could turn "read this file" into "delete that one" while still calling
    it by the skill's trusted name.
    """
    if skill.status is not SkillStatus.ACTIVE:
        raise SkillNotRunnable(
            f"{skill.name!r} is {skill.status.value}, not active — "
            "a skill runs against real data only after its tests pass and the owner approves"
        )
    version = skill.version()
    if version is None or not version.steps:
        raise SkillNotRunnable(f"{skill.name!r} has no steps to run")

    steps = _steps_of(version, inputs or {})
    return Plan(
        objective=skill.name,
        rationale=(
            f"running the learned skill {skill.name!r} (v{version.version}, "
            f"{len(steps)} steps) — {skill.description}"
        ),
        steps=steps,
    )


def _steps_of(version: SkillVersion, inputs: dict) -> list[PlanStep]:
    steps: list[PlanStep] = []
    previous: PlanStep | None = None
    for index, learned in enumerate(sorted(version.steps, key=lambda s: s.seq)):
        args = {**inputs, **learned.args}
        device_call = _is_device_call(learned)
        step = PlanStep(
            seq=index,
            kind=StepKind.AGENT,
            name=_agent_of(learned),
            objective=f"{learned.tool or learned.agent}: {learned.condition or 'as demonstrated'}",
            args={"action": canonical(learned.tool), "args": args} if device_call else args,
            # Linear, because a demonstration is a sequence and nothing in it says which
            # adjacent steps were independent.
            depends_on=[previous.id] if previous is not None else [],
            success_criteria=_criteria_for(learned, version, device_call),
        )
        steps.append(step)
        previous = step
    return steps


def _agent_of(learned) -> str:
    """The agent for one learned step — declared for an agent step, mapped for a tool."""
    return learned.agent if learned.is_agent_step else agent_for(learned.tool)


def _is_device_call(learned) -> bool:
    """Device commands take `{"action": ..., "args": ...}`; agent jobs take args directly."""
    return not learned.is_agent_step and agent_for(learned.tool) in ("computer", "browser")


def _criteria_for(learned, version: SkillVersion, device_call: bool) -> list[str]:
    criteria: list[str] = []
    if device_call:
        criteria.append("output.verified is true")
    for field in version.output_schema:
        criteria.append(f"output.{field} is not empty")
    # Not `output.summary` as a blanket floor: an agent that declares its own output schema
    # is checked against that by the Supervisor already, and asserting a field the agent
    # never promised fails work that succeeded.
    return criteria


def compose(
    name: str,
    description: str,
    parts: list[Skill],
) -> tuple[list, list[str]]:
    """Chain several skills into one step list — the skill composer (§53).

    "File Search + Data Analysis + Report Generation" becoming "Grade Report" is the
    example from the spec, and the mechanics are the easy half. The interesting half is what
    composition must *not* do:

    * It does not lower anything. The composed step list carries every step exactly as its
      source skill had it, so the risk of the whole is the union of the parts and a
      composed skill containing a destructive step still needs approval before activating.
    * It composes only ACTIVE skills. Chaining a draft into a composition would give the
      draft a way to run that the lifecycle exists to deny it.

    Returns the steps and the source skill names; the caller decides what to do with them,
    because a composition is a *draft* like any other captured workflow and must go through
    the same tests and approval.
    """
    from thursday_automation.skills.models import SkillStep

    inactive = [s.name for s in parts if s.status is not SkillStatus.ACTIVE]
    if inactive:
        raise SkillNotRunnable(
            "only active skills can be composed; these are not: " + ", ".join(inactive)
        )
    if len(parts) < 2:
        raise SkillNotRunnable("a composition needs at least two skills")

    steps: list[SkillStep] = []
    for skill in parts:
        version = skill.version()
        if version is None:
            continue
        for learned in sorted(version.steps, key=lambda s: s.seq):
            steps.append(
                SkillStep(
                    seq=len(steps),
                    tool=learned.tool,
                    agent=learned.agent,
                    args=dict(learned.args),
                    # Provenance in the condition text: months later, "which part of this
                    # came from where" is the first thing anyone editing it needs to know.
                    condition=learned.condition or f"from {skill.name}",
                    on_error=learned.on_error,
                )
            )
    if not steps:
        raise SkillNotRunnable("the skills being composed have no steps between them")
    return steps, [s.name for s in parts]
