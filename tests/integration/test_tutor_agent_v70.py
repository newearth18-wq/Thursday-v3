"""The Tutor agent, and rehearsing without doing (ADAPTIVE ONBOARDING) — Sprint 70.

Two things in this spec are shaped like ways around the Permission Engine, and both are here.

§48 lists what the tutor must never do — send email, delete files, purchase, change admin
settings, install software, alter permissions. The tempting implementation is a check inside
the tutor. That would be a second permission system, kept in agreement with the first by
hand, and its disagreements would be discovered by an agent doing something nobody
sanctioned. So the enforcement is structural: a READ ceiling and no tools.

§23's Practice Mode is worse, because the two implementations look identical from outside:
a flag the executor checks, or a description that is never executed. A flag means the real
path runs with `practice=True` in its hand, one missed branch away from sending the email.
This is the second kind, and the tests below are what say so.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from thursday_agents.tutor import NEVER, TUTOR_CAPABILITIES, TutorAgent
from thursday_core import practice
from thursday_core.plain import leaks
from thursday_shared.enums import PermissionLevel, PolicyDecision
from thursday_shared.ids import new_id
from thursday_shared.models import JobContract


def _contract(**inputs) -> JobContract:
    return JobContract(
        task_id=new_id(), step_id=new_id(), agent="tutor", objective="teach", inputs=inputs
    )


class _Ctx:
    """The minimum an agent's context has to be."""

    def __init__(self):
        from thursday_shared.models import Spend

        self.spend = Spend()
        self.events = []

    async def emit(self, event):
        self.events.append(event)


# ------------------------------------------------------------------ §48, structurally


def test_the_tutor_has_no_tools_at_all(container):
    """Not "only safe tools" — none. A tool list is a thing that grows in a hurry the first
    time somebody needs the tutor to demonstrate one more thing."""
    assert container.tutor.spec.tools == []


def test_the_tutor_sits_below_every_action_it_must_never_take(container):
    """§48's list is enforced by the ceiling, not by a check inside the tutor. Each of these
    resolves to a level above READ, so the Permission Engine refuses before the tutor is
    even a consideration."""
    table = container.permissions.policy
    assert container.tutor.spec.permission_ceiling is PermissionLevel.READ
    for action in NEVER:
        policy = table.get(action, autonomy=container.permissions.autonomy)
        assert policy.level > PermissionLevel.READ, action


def test_the_forbidden_list_is_data_rather_than_a_runtime_check(container):
    """If `NEVER` were consulted at runtime it would be a second permission system. It is
    kept so a test can assert unreachability — this test — and read by nothing else."""
    source = Path(inspect.getfile(TutorAgent)).read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "NEVER" and isinstance(node.ctx, ast.Load):
            raise AssertionError("NEVER is read at runtime; it should only be data for tests")


def test_the_tutor_only_claims_tutorial_capabilities(container):
    """§47, and the reason it is a namespace: `tutorial.*` has no executor behind it, so the
    ceiling is real rather than declared."""
    assert set(container.tutor.spec.capabilities) == set(TUTOR_CAPABILITIES)
    for capability in TUTOR_CAPABILITIES:
        assert capability.split(".")[0] in {"tutorial", "ui", "demo", "capability"}


def test_teaching_never_leaves_the_machine(container):
    """Lessons are about the owner's own progress on their own machine. LOCAL_ONLY says so
    rather than trusting that nothing sensitive ever ends up in a lesson prompt."""
    assert container.tutor.spec.privacy_profile == "local_only"


def test_the_tutor_is_registered_like_any_other_agent(container):
    """No side door. It goes in the same registry, is selected by the same router, and is
    judged by the same Supervisor."""
    assert "tutor" in {spec.name for spec in container.agents.specs()}


# ---------------------------------------------------------- §23 practice does not execute


def test_practice_mode_cannot_reach_an_executor():
    """The structural claim, checked against imports. A module that never imports the
    engine, the hub or a tool registry cannot dispatch anything — which is a fact about the
    module rather than a promise in its docstring."""
    tree = ast.parse(Path(practice.__file__).read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    forbidden = {
        "thursday_core.execution",
        "thursday_core.engine",
        "thursday_core.orchestrator",
        "thursday_devices.hub",
        "thursday_tools.registry",
        "thursday_tools.builtin",
    }
    assert not (imported & forbidden), imported & forbidden


def test_practice_never_constructs_an_action_request():
    """An `ActionRequest` is the object the Permission Engine decides on. Building one for an
    action nobody intends to take teaches the engine that `email.send` was approved — the
    approval state is real even when the send is not.

    Walked as an AST rather than grepped. The first version of this test searched the source
    text and matched the module's own docstring saying it does not do this — the same mistake
    as Sprint 46's "curl" scan, Sprint 52's docstring match, and Sprint 69's `.get()` scan.
    Prose about code is not code, and only one of the two is worth asserting on.
    """
    tree = ast.parse(Path(practice.__file__).read_text())

    called: set[str] = set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                called.add(func.attr)
            elif isinstance(func, ast.Name):
                called.add(func.id)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)

    assert "ActionRequest" not in names and "ActionRequest" not in called
    for dispatch in ("decide", "execute", "invoke", "request", "approve"):
        assert dispatch not in called, f"practice.py calls {dispatch}()"


def test_rehearse_takes_no_flag_that_could_make_it_real():
    parameters = set(inspect.signature(practice.rehearse).parameters)
    assert parameters == {"container", "action", "resource"}
    for forbidden in ("execute", "real", "dry_run", "commit", "apply", "practice"):
        assert forbidden not in parameters


def test_every_rehearsal_states_that_nothing_happened(container):
    """Said in the payload rather than implied by the endpoint, so a client rendering this
    has no way to think something occurred."""
    for rendered in practice.offers(container):
        assert rendered["happened"] is False
        assert rendered["practice"] is True


def test_rehearsing_a_delete_leaves_no_approval_behind(container):
    """The observable version of "it did not execute": rehearsing a dangerous action must
    not create pending approval state, because that state is real."""
    before = len(container.approvals.pending())
    practice.rehearse(container, "file.delete", resource="report.xlsx")
    practice.rehearse(container, "email.send")
    assert len(container.approvals.pending()) == before


def test_a_rehearsal_reports_the_decision_the_real_path_would_make(container):
    """Read from the same table, so the rehearsal cannot describe a rule that is not in
    force. If `file.delete` were ever relaxed, this rehearsal would change with it."""
    table = container.permissions.policy
    for action in practice.OFFERED:
        rehearsal = practice.rehearse(container, action)
        expected = table.get(action, autonomy=container.permissions.autonomy)
        assert rehearsal.decision is expected.default
        assert rehearsal.reversible == expected.reversible


def test_the_dangerous_rehearsals_all_ask(container):
    """§24's list — delete, send, automate — are exactly the ones that must never be AUTO.
    Rehearsing them is how somebody meets that fact with nothing at stake."""
    for action in ("file.delete", "email.send"):
        assert practice.rehearse(container, action).decision is PolicyDecision.ASK_ALWAYS


def test_a_rehearsal_shows_the_words_the_owner_would_actually_see(container):
    rehearsal = practice.rehearse(container, "email.send")
    assert rehearsal.prompt
    assert "ไหม" in rehearsal.prompt
    assert leaks(rehearsal.prompt) == []


def test_an_action_nobody_wrote_a_sentence_for_is_not_described_by_its_name(container):
    """Sprint 65's rule. "system.process.start" rendered into a sentence is not a sentence
    anybody wanted to read."""
    rehearsal = practice.rehearse(container, "system.process.start")
    assert "system.process.start" not in rehearsal.would
    assert leaks(rehearsal.would) == []


# ------------------------------------------------------------- §25/§26/§35 explaining


def test_why_it_asked_is_read_from_the_table_not_recalled(container):
    """§35. The alternative is Thursday generating a sentence about a decision it no longer
    has in front of it. Reading the rule back is cheaper and true."""
    explanation = practice.explain_decision(container, "file.delete")
    assert explanation
    assert leaks(explanation) == []
    assert "ถาม" in explanation


def test_the_explanation_says_the_rule_can_be_changed(container):
    """§25's own example ends with "คุณสามารถเปลี่ยนกฎนี้ได้ภายหลัง" — the part that turns
    an interruption into something the owner has a say in."""
    assert "เปลี่ยน" in practice.explain_decision(container, "email.send")


# ------------------------------------------------------------------------ what it teaches


async def test_asking_what_it_can_do_returns_areas_not_a_list_of_everything(container):
    result = await container.tutor.run(_contract(), _Ctx())
    assert result.ok
    assert "ด้าน" in result.output["explanation"]
    assert 1 <= len(result.output["areas"]) <= 8


async def test_explaining_an_unavailable_feature_says_so(container):
    """§11: "ห้ามแนะนำ Feature ที่เครื่องยังไม่รองรับโดยไม่บอกข้อจำกัด"."""
    result = await container.tutor.run(_contract(topic="สอนใช้กล้องหน่อย"), _Ctx())
    assert result.ok
    assert result.output["available"] is False
    assert "กล้อง" in result.output["explanation"]
    assert "มือถือ" in result.output["explanation"]


async def test_an_unrecognised_topic_says_so_rather_than_guessing(container):
    """Guessing wrong means confidently explaining the wrong feature, which is worse than
    admitting it and offering a starting point."""
    result = await container.tutor.run(_contract(topic="ทำอาหาร"), _Ctx())
    assert "ยังไม่แน่ใจ" in result.output["explanation"]


async def test_the_tutor_answers_i_do_not_know_how_to_use_this(container):
    """§68's opening line: "Thursday ฉันใช้ไม่เป็น"."""
    result = await container.tutor.run(_contract(question="ฉันใช้ไม่เป็น"), _Ctx())
    assert result.ok
    assert result.output["explanation"]


async def test_nothing_the_tutor_says_leaks_an_internal(container):
    for inputs in ({}, {"topic": "กล้อง"}, {"topic": "agent"}, {"lesson_id": "open-an-app"}):
        result = await container.tutor.run(_contract(**inputs), _Ctx())
        assert leaks(str(result.output)) == [], inputs


# ------------------------------------------------------------------------- §67 privacy


def test_the_tutor_has_no_way_to_read_what_thursday_remembers(container):
    """§67: the tutor must never expose memory, secrets, prompts or credentials. It has no
    tools, and its own source never touches the memory store or the vault."""
    source = Path(inspect.getfile(TutorAgent)).read_text()
    for forbidden in (".memory", ".vault", ".secrets", "recall(", "export_state"):
        assert forbidden not in source, forbidden


async def test_the_tutor_never_reports_what_the_owner_told_thursday_to_remember(container):
    """The behavioural half. A memory exists; nothing the tutor says contains it."""
    from thursday_shared.enums import MemoryLayer, MemorySource
    from thursday_shared.models import MemoryWrite

    secret = "เลขบัญชีของฉันคือ 123-456-789"
    await container.memory.write(
        MemoryWrite(layer=MemoryLayer.SEMANTIC, content=secret, source=MemorySource.USER)
    )
    for inputs in ({}, {"topic": "จำ"}, {"question": "นายรู้อะไรเกี่ยวกับฉันบ้าง"}):
        result = await container.tutor.run(_contract(**inputs), _Ctx())
        assert "123-456-789" not in str(result.output)
        assert secret not in str(result.output)


# ------------------------------------------------------------------------ §46 bookkeeping


def test_the_tutor_reports_the_next_lesson_and_the_path(container):
    assert container.tutor.suggest()["id"] == "say-something"
    stages = container.tutor.learning_path()
    assert stages and all("lessons" in stage for stage in stages)


def test_the_tutor_describes_itself_to_a_person(container):
    """It is subject to §61 like anything else — and the sentence is about what it does for
    the owner, not about what it is."""
    spec = container.tutor.spec
    assert spec.user_description
    assert leaks(spec.user_description) == []
    assert spec.safety_notes
