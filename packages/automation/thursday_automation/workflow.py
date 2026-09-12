"""The node graph behind the workflow builder (§48, V15).

A visual builder has one failure mode that matters, and it is not a missing feature. It is
a canvas that lets the owner draw something the engine cannot run, or can run but not the
way the picture reads. Then the rule is saved, looks right, and does something else — and
the owner finds out at the moment the rule mattered.

So the graph here is **the engine's shape, drawn**, not a general dataflow language:

    [trigger] ──▶ [condition] ──▶ [condition] ──▶ [action] ──▶ [action]
                   all must hold                  in order, then follow-ups

Exactly one trigger. Conditions are an AND-set — the engine evaluates `all(...)`, so there
is no OR node, no branch and no loop, because drawing one would be drawing a promise.
Actions run in order, and `follow_ups` is the tail of the same list marked as such.

Three things this module does that the canvas alone could not:

**It converts, both ways.** `to_automation` and `from_automation` round-trip, so a rule
written by `routines.py` or restored from disk opens in the builder instead of being
invisible to it.

**It refuses rather than corrects.** `validate` returns every problem with the node that
caused it. A graph with a problem does not save; nothing here quietly drops a dangling node
or invents a missing field.

**It says what would happen, before it happens.** `preview` reports, per action, what the
Permission Engine would decide and whether a handler for that kind of action is even wired
up — the unwired case being the one a picture hides best (ADR 0064).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from thursday_shared.enums import NotificationPriority, PermissionLevel, PolicyDecision
from thursday_shared.ids import new_id
from thursday_shared.models import Budget

from thursday_automation.cron import CronRefused, describe, parse
from thursday_automation.rules import Action, Automation, Condition, Trigger

NodeKind = Literal["trigger", "condition", "action"]

#: Trigger kinds the builder offers, and what actually runs them. A kind with no runner is
#: listed here with the reason rather than left off the menu silently — the owner asking
#: "why can't I do X" deserves the answer, and a future runner turns one line green.
TRIGGER_RUNNERS: dict[str, str] = {
    "event": "",
    "schedule": "",
    "manual": "",
    "state_change": (
        "ยังไม่มีตัวเฝ้าดูสถานะ — ไม่มีโค้ดส่วนไหนคอยดูว่าค่าใน world state เปลี่ยน กฎที่ใช้ตัวกระตุ้นนี้จะไม่ทำงานเลย"
    ),
}

#: Action kinds, and the container attribute each needs in order to do anything. The engine
#: returns `{"skipped": kind}` when the attribute is absent, which from the outside looks
#: exactly like a rule that ran and found nothing to do.
ACTION_NEEDS: dict[str, str] = {
    "notify": "",
    "task": "tasks",
    "tool": "executor",
    "obsidian_write": "executor",
}

CONDITION_OPS: tuple[str, ...] = ("eq", "ne", "gt", "lt", "contains", "matches", "in")


class WorkflowRefused(ValueError):
    """A graph this module will not turn into a rule."""


@dataclass
class Node:
    """One box on the canvas. `x`/`y` are the owner's layout and mean nothing to the engine."""

    id: str
    kind: NodeKind
    #: For a trigger: the trigger kind. For a condition: the operator. For an action: the
    #: action kind. Always the thing that decides how `config` is read.
    subkind: str = ""
    config: dict[str, Any] = field(default_factory=dict)
    x: float = 0.0
    y: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "subkind": self.subkind,
            "config": dict(self.config),
            "x": self.x,
            "y": self.y,
        }


@dataclass
class Problem:
    """Something wrong, and which box it is in. `node` is empty for a whole-graph problem."""

    node: str
    message: str
    #: "error" stops the rule saving. "warning" does not, and must never be used for
    #: something that makes the rule not run — that is an error wearing a softer word.
    severity: Literal["error", "warning"] = "error"

    def to_dict(self) -> dict[str, Any]:
        return {"node": self.node, "message": self.message, "severity": self.severity}


@dataclass
class Workflow:
    """A graph, and the rule it means."""

    name: str = ""
    nodes: list[Node] = field(default_factory=list)
    #: Ordered: the action order on the canvas is the order the engine runs them in.
    order: list[str] = field(default_factory=list)
    #: Where the follow-up tail starts within the action nodes. `None` means no follow-ups.
    follow_up_from: int | None = None
    enabled: bool = False
    automation_id: UUID | None = None
    priority: NotificationPriority = NotificationPriority.NORMAL
    budget: Budget = field(default_factory=lambda: Budget(usd=0.20, seconds=120, tool_calls=10))

    # ----------------------------------------------------------------- shape

    @property
    def triggers(self) -> list[Node]:
        return [n for n in self.nodes if n.kind == "trigger"]

    @property
    def conditions(self) -> list[Node]:
        return [n for n in self.nodes if n.kind == "condition"]

    @property
    def actions(self) -> list[Node]:
        """Action nodes in the order the owner arranged them."""
        by_id = {n.id: n for n in self.nodes if n.kind == "action"}
        ordered = [by_id.pop(node_id) for node_id in self.order if node_id in by_id]
        # Anything the order forgot still appears, at the end. Dropping it would make a
        # node vanish from the rule while staying on the canvas.
        return [*ordered, *by_id.values()]

    # ------------------------------------------------------------ validation

    def validate(self) -> list[Problem]:
        """Every reason this graph is not a rule. Empty means it is."""
        problems: list[Problem] = []

        if not self.name.strip():
            problems.append(Problem("", "กฎต้องมีชื่อ"))

        triggers = self.triggers
        if not triggers:
            problems.append(Problem("", "ต้องมีตัวกระตุ้น (WHEN) หนึ่งอัน"))
        elif len(triggers) > 1:
            # The engine has one `trigger` field. Two on the canvas would mean one is
            # silently ignored, and the picture would not say which.
            problems.append(Problem("", f"มีตัวกระตุ้น {len(triggers)} อัน — เครื่องมือนี้รับได้อันเดียว"))

        for node in triggers:
            problems.extend(self._trigger_problems(node))
        for node in self.conditions:
            problems.extend(self._condition_problems(node))

        actions = self.actions
        if not actions:
            problems.append(Problem("", "ต้องมีการกระทำ (DO) อย่างน้อยหนึ่งอย่าง"))
        for node in actions:
            problems.extend(self._action_problems(node))

        if self.follow_up_from is not None and not 0 < self.follow_up_from <= len(actions):
            problems.append(
                Problem("", f"จุดเริ่ม follow-up ({self.follow_up_from}) อยู่นอกลำดับการกระทำ")
            )

        return problems

    def _trigger_problems(self, node: Node) -> list[Problem]:
        if node.subkind not in TRIGGER_RUNNERS:
            return [Problem(node.id, f"ไม่รู้จักตัวกระตุ้นชนิด {node.subkind!r}")]

        unavailable = TRIGGER_RUNNERS[node.subkind]
        if unavailable:
            return [Problem(node.id, unavailable)]

        if node.subkind == "schedule":
            try:
                parse(str(node.config.get("cron") or ""))
            except CronRefused as exc:
                return [Problem(node.id, str(exc))]
        if node.subkind == "event" and not str(node.config.get("event_kind") or "").strip():
            return [Problem(node.id, "ตัวกระตุ้นแบบเหตุการณ์ต้องระบุชนิดเหตุการณ์")]
        return []

    def _condition_problems(self, node: Node) -> list[Problem]:
        problems: list[Problem] = []
        if node.subkind not in CONDITION_OPS:
            problems.append(Problem(node.id, f"ไม่รู้จักตัวเปรียบเทียบ {node.subkind!r}"))
        if not str(node.config.get("field") or "").strip():
            problems.append(Problem(node.id, "เงื่อนไขต้องระบุฟิลด์ที่จะตรวจ"))
        if node.subkind in ("gt", "lt"):
            try:
                float(node.config.get("value"))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                # `gt` on a non-number compares NaN, which is False for everything — a
                # condition that never holds, and a rule that never fires, with no error.
                problems.append(
                    Problem(
                        node.id, f"{node.subkind} ต้องเทียบกับตัวเลข — ได้ {node.config.get('value')!r}"
                    )
                )
        if node.subkind == "in" and not isinstance(node.config.get("value"), list):
            problems.append(Problem(node.id, "in ต้องเทียบกับรายการ"))
        return problems

    def _action_problems(self, node: Node) -> list[Problem]:
        if node.subkind not in ACTION_NEEDS:
            return [Problem(node.id, f"ไม่รู้จักการกระทำชนิด {node.subkind!r}")]
        if node.subkind == "tool" and not str(node.config.get("name") or "").strip():
            return [Problem(node.id, "การกระทำแบบเครื่องมือต้องระบุชื่อเครื่องมือ")]
        if node.subkind == "notify" and not str(node.config.get("title") or "").strip():
            return [Problem(node.id, "การแจ้งเตือนต้องมีหัวข้อ", "warning")]
        return []

    # ------------------------------------------------------------ conversion

    def to_automation(self) -> Automation:
        """The rule this graph means. Refuses a graph that has errors."""
        errors = [p for p in self.validate() if p.severity == "error"]
        if errors:
            raise WorkflowRefused(" / ".join(p.message for p in errors))

        trigger_node = self.triggers[0]
        trigger = Trigger(kind=trigger_node.subkind)  # type: ignore[arg-type]
        if trigger.kind == "event":
            trigger.event_kind = str(trigger_node.config.get("event_kind") or "*")
        elif trigger.kind == "schedule":
            trigger.cron = str(trigger_node.config.get("cron"))
        elif trigger.kind == "state_change":  # pragma: no cover - validate refuses first
            trigger.field = str(trigger_node.config.get("field") or "")

        actions = self.actions
        split = self.follow_up_from if self.follow_up_from is not None else len(actions)

        return Automation(
            id=self.automation_id or new_id(),
            name=self.name.strip(),
            trigger=trigger,
            conditions=[
                Condition(
                    field=str(n.config.get("field")),
                    op=n.subkind,  # type: ignore[arg-type]
                    value=n.config.get("value"),
                )
                for n in self.conditions
            ],
            actions=[_action(n) for n in actions[:split]],
            follow_ups=[_action(n) for n in actions[split:]],
            enabled=self.enabled,
            priority=self.priority,
            budget=self.budget,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "nodes": [n.to_dict() for n in self.nodes],
            "order": list(self.order),
            "follow_up_from": self.follow_up_from,
            "enabled": self.enabled,
            "automation_id": str(self.automation_id) if self.automation_id else None,
            "priority": str(self.priority),
            "count": len(self.nodes),
            "items": [n.to_dict() for n in self.nodes],
        }


def _action(node: Node) -> Action:
    """An action node's config is `name` plus everything else, which becomes `args`.

    Flat rather than nested, because the canvas edits fields and a nested `args` object
    would need an editor of its own — and a field the owner cannot see is a field that
    quietly keeps whatever the last person put in it.
    """
    config = dict(node.config)
    return Action(
        kind=node.subkind,  # type: ignore[arg-type]
        name=str(config.pop("name", "")),
        args=config,
        device_hint=None,
    )


def from_automation(
    automation: Automation, *, layout: dict[str, tuple[float, float]] | None = None
) -> Workflow:
    """Open an existing rule on the canvas.

    Rules written by `routines.py`, restored from disk, or suggested by Thursday are all
    ordinary automations. Without this they would be invisible to the builder, and the
    owner would have two places rules live — one they can see and one they cannot.
    """
    places = layout or {}
    nodes: list[Node] = []
    order: list[str] = []

    def place(node_id: str, column: int, row: int) -> tuple[float, float]:
        return places.get(node_id, (120.0 + column * 240.0, 80.0 + row * 110.0))

    trigger_id = "trigger"
    config: dict[str, Any] = {}
    if automation.trigger.kind == "event":
        config["event_kind"] = automation.trigger.event_kind
    elif automation.trigger.kind == "schedule":
        config["cron"] = automation.trigger.cron or ""
    elif automation.trigger.kind == "state_change":
        config["field"] = automation.trigger.field or ""
    x, y = place(trigger_id, 0, 0)
    nodes.append(Node(trigger_id, "trigger", automation.trigger.kind, config, x, y))

    for index, condition in enumerate(automation.conditions):
        node_id = f"condition-{index}"
        x, y = place(node_id, 1, index)
        nodes.append(
            Node(
                node_id,
                "condition",
                condition.op,
                {"field": condition.field, "value": condition.value},
                x,
                y,
            )
        )

    every = [*automation.actions, *automation.follow_ups]
    for index, action in enumerate(every):
        node_id = f"action-{index}"
        x, y = place(node_id, 2, index)
        nodes.append(
            Node(node_id, "action", action.kind, {"name": action.name, **action.args}, x, y)
        )
        order.append(node_id)

    return Workflow(
        name=automation.name,
        nodes=nodes,
        order=order,
        follow_up_from=len(automation.actions) if automation.follow_ups else None,
        enabled=automation.enabled,
        automation_id=automation.id,
        priority=automation.priority,
        budget=automation.budget,
    )


# ------------------------------------------------------------------------ preview


@dataclass
class Consequence:
    """What one action node would actually do, if the rule fired right now."""

    node: str
    kind: str
    #: The permission verb this resolves to, as the Permission Engine would see it.
    action: str
    level: PermissionLevel
    decision: PolicyDecision
    blocked: bool
    #: Empty when the action can run. Otherwise why it cannot, in the owner's words.
    unavailable: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "node": self.node,
            "kind": self.kind,
            "action": self.action,
            # `.name`, not `str()`: PermissionLevel is an IntEnum, so str() gives "3" and
            # the screen would show a number nobody can read.
            "level": self.level.name,
            "decision": str(self.decision),
            "blocked": self.blocked,
            "unavailable": self.unavailable,
        }


def preview(
    workflow: Workflow, *, policies: Any, wired: set[str] | None = None
) -> list[Consequence]:
    """What each action would do — permission decision first, then whether it can run at all.

    `wired` names the container attributes that are actually present. An action whose
    handler is missing returns `{"skipped": ...}` from the engine, which is indistinguishable
    from a rule that ran and had nothing to do. Saying so here, while the owner is looking
    at the canvas, is the only moment it is cheap to say.
    """
    present = wired if wired is not None else set(ACTION_NEEDS.values())
    out: list[Consequence] = []

    for node in workflow.actions:
        verb = _verb(node)
        policy = policies.get(verb)
        needs = ACTION_NEEDS.get(node.subkind, "")
        unavailable = ""
        if needs and needs not in present:
            unavailable = f"ยังไม่ได้ต่อ {needs} — การกระทำนี้จะถูกข้ามไปเงียบ ๆ"
        out.append(
            Consequence(
                node=node.id,
                kind=node.subkind,
                action=verb,
                level=policy.level,
                decision=policy.default,
                blocked=bool(getattr(policies, "is_blocked", lambda _n: False)(verb)),
                unavailable=unavailable,
            )
        )
    return out


def _verb(node: Node) -> str:
    """The permission verb an action node resolves to."""
    if node.subkind == "tool":
        return str(node.config.get("name") or "")
    if node.subkind == "obsidian_write":
        return "obsidian.write"
    if node.subkind == "notify":
        return "notify.show"
    return "task.create"


def explain(workflow: Workflow) -> str:
    """One line the owner can check against what they meant. No graph, no jargon."""
    trigger = workflow.triggers[0] if workflow.triggers else None
    if trigger is None:
        return "ยังไม่มีตัวกระตุ้น"

    if trigger.subkind == "schedule":
        try:
            when = describe(str(trigger.config.get("cron") or ""))
        except CronRefused:
            when = "ตารางเวลาที่อ่านไม่ออก"
    elif trigger.subkind == "event":
        when = f"เมื่อเกิด {trigger.config.get('event_kind') or '*'}"
    elif trigger.subkind == "manual":
        when = "เมื่อสั่งเอง"
    else:
        when = f"เมื่อ {trigger.subkind}"

    parts = [when]
    if workflow.conditions:
        parts.append(f"ถ้าเข้าเงื่อนไขครบทั้ง {len(workflow.conditions)} ข้อ")
    actions = workflow.actions
    if actions:
        parts.append("แล้ว " + " → ".join(_name(n) for n in actions))
    return " ".join(parts)


def _name(node: Node) -> str:
    if node.subkind == "tool":
        return str(node.config.get("name") or "เครื่องมือ")
    return {"notify": "แจ้งเตือน", "task": "สร้างงาน", "obsidian_write": "บันทึกลง Obsidian"}.get(
        node.subkind, node.subkind
    )
