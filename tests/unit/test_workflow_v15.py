"""The workflow graph: what it refuses, what it converts, and what it warns about (V15)."""

from __future__ import annotations

import pytest
from thursday_automation.rules import Action, Automation, Condition, Trigger
from thursday_automation.workflow import (
    ACTION_NEEDS,
    TRIGGER_RUNNERS,
    Node,
    Workflow,
    WorkflowRefused,
    explain,
    from_automation,
    preview,
)
from thursday_security.policy import PolicyTable


def graph(**overrides) -> Workflow:
    base = {
        "name": "เตือนตอนเช้า",
        "nodes": [
            Node("t", "trigger", "schedule", {"cron": "30 7 * * 1-5"}),
            Node("a", "action", "notify", {"title": "สรุปงานวันนี้"}),
        ],
        "order": ["a"],
    }
    return Workflow(**{**base, **overrides})


def errors(workflow: Workflow) -> list[str]:
    return [p.message for p in workflow.validate() if p.severity == "error"]


# ------------------------------------------------------------------- what it refuses


def test_a_complete_graph_has_nothing_to_say():
    assert graph().validate() == []


def test_a_graph_with_no_trigger_is_refused():
    assert any(
        "ตัวกระตุ้น" in m for m in errors(graph(nodes=[Node("a", "action", "notify", {"title": "x"})]))
    )


def test_two_triggers_are_refused_rather_than_one_being_ignored():
    """The engine has one `trigger` field. A second box on the canvas would be silently
    dropped, and the picture would not say which one survived."""
    two = graph(
        nodes=[
            Node("t", "trigger", "manual"),
            Node("t2", "trigger", "event", {"event_kind": "file.created"}),
            Node("a", "action", "notify", {"title": "x"}),
        ]
    )
    assert any("อันเดียว" in m for m in errors(two))


def test_a_graph_with_no_action_is_refused():
    assert any(
        "การกระทำ" in m for m in errors(graph(nodes=[Node("t", "trigger", "manual")], order=[]))
    )


def test_an_unreadable_schedule_is_refused_on_the_node_that_holds_it():
    bad = graph(
        nodes=[
            Node("t", "trigger", "schedule", {"cron": "ทุกเช้า"}),
            Node("a", "action", "notify", {"title": "x"}),
        ]
    )
    problems = [p for p in bad.validate() if p.severity == "error"]
    assert problems and problems[0].node == "t"


def test_a_numeric_comparison_against_a_word_is_refused():
    """`gt` on a non-number compares NaN, which is False for everything: a condition that
    never holds and a rule that never fires, with no error anywhere."""
    with_condition = graph(
        nodes=[
            Node("t", "trigger", "manual"),
            Node("c", "condition", "gt", {"field": "event.size", "value": "ใหญ่"}),
            Node("a", "action", "notify", {"title": "x"}),
        ]
    )
    assert any("ตัวเลข" in m for m in errors(with_condition))


def test_in_needs_a_list():
    node = Node("c", "condition", "in", {"field": "event.kind", "value": "file.created"})
    assert any(
        "รายการ" in m
        for m in errors(
            graph(
                nodes=[
                    Node("t", "trigger", "manual"),
                    node,
                    Node("a", "action", "notify", {"title": "x"}),
                ]
            )
        )
    )


def test_a_trigger_nothing_runs_is_refused_with_the_reason():
    """`state_change` has been in the Trigger type since the engine was written and nothing
    has ever watched a world-state field. Offering it silently would be the whole failure
    this builder exists to avoid."""
    assert TRIGGER_RUNNERS["state_change"]
    stale = graph(
        nodes=[
            Node("t", "trigger", "state_change", {"field": "owner_status"}),
            Node("a", "action", "notify", {"title": "x"}),
        ]
    )
    assert any("ยังไม่มีตัวเฝ้าดู" in m for m in errors(stale))


def test_a_missing_notification_title_is_a_warning_not_an_error():
    """It still runs — the engine falls back to the rule's name. A warning that stopped the
    save would be an error wearing a softer word; one that hides a rule that cannot run
    would be the opposite mistake."""
    untitled = graph(nodes=[Node("t", "trigger", "manual"), Node("a", "action", "notify", {})])
    severities = {p.severity for p in untitled.validate()}
    assert severities == {"warning"}
    assert untitled.to_automation().name == "เตือนตอนเช้า"


def test_a_graph_with_errors_will_not_convert():
    with pytest.raises(WorkflowRefused):
        graph(name="  ").to_automation()


# --------------------------------------------------------------------- conversion


def test_the_graph_becomes_the_rule_it_draws():
    workflow = graph(
        nodes=[
            Node("t", "trigger", "event", {"event_kind": "file.created"}),
            Node("c", "condition", "contains", {"field": "event.path", "value": "งาน"}),
            Node("a", "action", "tool", {"name": "file.read", "path": "/tmp/x"}),
            Node("b", "action", "notify", {"title": "อ่านแล้ว"}),
        ],
        order=["a", "b"],
    )
    automation = workflow.to_automation()
    assert automation.trigger.kind == "event" and automation.trigger.event_kind == "file.created"
    assert [c.op for c in automation.conditions] == ["contains"]
    assert [a.kind for a in automation.actions] == ["tool", "notify"]
    assert automation.actions[0].name == "file.read"
    assert automation.actions[0].args == {"path": "/tmp/x"}


def test_the_canvas_order_is_the_run_order():
    workflow = graph(
        nodes=[
            Node("t", "trigger", "manual"),
            Node("a", "action", "notify", {"title": "หนึ่ง"}),
            Node("b", "action", "notify", {"title": "สอง"}),
        ],
        order=["b", "a"],
    )
    assert [a.args["title"] for a in workflow.to_automation().actions] == ["สอง", "หนึ่ง"]


def test_a_node_the_order_forgot_still_reaches_the_rule():
    """Dropping it would make a box vanish from the rule while staying on the canvas —
    the exact divergence between picture and behaviour this module exists to prevent."""
    workflow = graph(
        nodes=[
            Node("t", "trigger", "manual"),
            Node("a", "action", "notify", {"title": "หนึ่ง"}),
            Node("orphan", "action", "notify", {"title": "สอง"}),
        ],
        order=["a"],
    )
    assert len(workflow.to_automation().actions) == 2


def test_follow_ups_are_the_tail_of_the_same_order():
    workflow = graph(
        nodes=[
            Node("t", "trigger", "manual"),
            Node("a", "action", "notify", {"title": "หนึ่ง"}),
            Node("b", "action", "notify", {"title": "สอง"}),
        ],
        order=["a", "b"],
        follow_up_from=1,
    )
    automation = workflow.to_automation()
    assert [a.args["title"] for a in automation.actions] == ["หนึ่ง"]
    assert [a.args["title"] for a in automation.follow_ups] == ["สอง"]


def test_a_follow_up_split_past_the_end_is_refused():
    assert any("follow-up" in m for m in errors(graph(follow_up_from=9)))


def test_an_existing_rule_opens_on_the_canvas():
    """Rules written by routines.py or restored from disk are ordinary automations. Without
    this the owner has two places rules live: one they can see and one they cannot."""
    automation = Automation(
        name="สรุปเย็น",
        trigger=Trigger(kind="schedule", cron="0 17 * * *"),
        conditions=[Condition(field="world.owner_status", op="eq", value="available")],
        actions=[Action(kind="notify", args={"title": "สรุป"})],
        follow_ups=[Action(kind="task", name="เขียนบันทึก")],
    )
    workflow = from_automation(automation)
    assert workflow.name == "สรุปเย็น"
    assert workflow.triggers[0].config["cron"] == "0 17 * * *"
    assert len(workflow.conditions) == 1
    assert workflow.follow_up_from == 1
    assert workflow.validate() == []


def test_the_round_trip_keeps_the_rule_the_same():
    original = graph(
        nodes=[
            Node("t", "trigger", "event", {"event_kind": "task.completed"}),
            Node("c", "condition", "eq", {"field": "event.ok", "value": True}),
            Node("a", "action", "tool", {"name": "notify.show", "title": "เสร็จ"}),
        ],
        order=["a"],
    ).to_automation()
    again = from_automation(original).to_automation()
    assert again.trigger.kind == original.trigger.kind
    assert again.trigger.event_kind == original.trigger.event_kind
    assert [(c.field, c.op, c.value) for c in again.conditions] == [
        (c.field, c.op, c.value) for c in original.conditions
    ]
    assert [(a.kind, a.name, a.args) for a in again.actions] == [
        (a.kind, a.name, a.args) for a in original.actions
    ]


def test_a_rule_opened_from_disk_keeps_its_identity():
    """A new id on open would save a second copy every time the owner looked at one."""
    automation = Automation(
        name="x",
        trigger=Trigger(kind="manual"),
        actions=[Action(kind="notify", args={"title": "y"})],
    )
    assert from_automation(automation).to_automation().id == automation.id


# ------------------------------------------------------- what would actually happen


def test_the_permission_decision_is_shown_while_the_rule_is_still_being_drawn():
    workflow = graph(
        nodes=[
            Node("t", "trigger", "manual"),
            Node("a", "action", "tool", {"name": "file.delete"}),
        ],
        order=["a"],
    )
    (consequence,) = preview(workflow, policies=PolicyTable(), wired={"executor"})
    assert consequence.level.name == "MODIFY"
    assert consequence.decision.value == "ASK_ALWAYS"
    assert not consequence.blocked


def test_an_action_in_the_block_set_says_so_on_the_canvas():
    """Finding out at 3am that the rule could never have run is finding out too late."""
    workflow = graph(
        nodes=[
            Node("t", "trigger", "manual"),
            Node("a", "action", "tool", {"name": "security.disable"}),
        ],
        order=["a"],
    )
    (consequence,) = preview(workflow, policies=PolicyTable(), wired={"executor"})
    assert consequence.blocked and consequence.decision.value == "BLOCK"


def test_an_action_whose_handler_is_not_wired_is_named_as_such():
    """The engine returns `{"skipped": kind}` when the executor is absent, which from the
    outside is indistinguishable from a rule that ran and found nothing to do."""
    assert ACTION_NEEDS["tool"] == "executor"
    workflow = graph(
        nodes=[Node("t", "trigger", "manual"), Node("a", "action", "tool", {"name": "file.read"})],
        order=["a"],
    )
    (consequence,) = preview(workflow, policies=PolicyTable(), wired=set())
    assert "ยังไม่ได้ต่อ executor" in consequence.unavailable


def test_a_notification_needs_nothing_wired():
    workflow = graph()
    (consequence,) = preview(workflow, policies=PolicyTable(), wired=set())
    assert consequence.unavailable == ""


# --------------------------------------------------------------------- plain words


def test_the_rule_reads_back_as_one_sentence():
    assert explain(graph()) == "07:30 วันจันทร์ถึงศุกร์ แล้ว แจ้งเตือน"


def test_no_conditions_means_no_sentence_about_conditions():
    """ "ถ้าเข้าเงื่อนไขครบทั้ง 0 ข้อ" is true and unreadable."""
    assert "เงื่อนไข" not in explain(graph())
    with_one = graph(
        nodes=[
            Node("t", "trigger", "manual"),
            Node("c", "condition", "eq", {"field": "event.ok", "value": True}),
            Node("a", "action", "notify", {"title": "x"}),
        ],
    )
    assert "เงื่อนไขครบทั้ง 1 ข้อ" in explain(with_one)


def test_an_unreadable_schedule_does_not_get_a_confident_sentence():
    bad = graph(
        nodes=[
            Node("t", "trigger", "schedule", {"cron": "ทุกเช้า"}),
            Node("a", "action", "notify", {"title": "x"}),
        ]
    )
    assert "อ่านไม่ออก" in explain(bad)
