"""Release readiness (Sprint 50).

Every sprint in this project found the same class of bug: something the code documented about
itself had quietly stopped being true. A barge-in that never fired, an `IntentKind.APPROVE`
nothing produced, a skill matcher scoring backwards, a policy flag threaded through three
layers and read by none, a redactor whose docstring said it ran on every prompt, a metrics
fallback that made every action look identical. None of them failed a test, because no test
asked.

So the release gate is not a checklist somebody ticks. It is this file, which asks the
questions that would have caught those bugs, mechanically, on every run:

  · does everything the container declares actually get built?
  · does every port have both adapters, so the offline claim is real?
  · does every action have a policy, and every agent a contract?
  · does every ADR exist, get indexed, and point at files that are still there?
  · do the counts the README states match the repository?

None of this proves Thursday is good. It proves the documentation is not lying, which is the
part that decays silently and the part a reader has no way to check.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest
from thursday_devices.actions import CATALOGUE
from thursday_security.policy import PolicyTable
from thursday_shared.enums import PolicyDecision

#: A count with or without a thousands separator. The first version was `\d{3,4}`, which read
#: "1,010 tests" as ten — and then reported that the README claimed fewer tests than there are
#: test functions. The check was right to fail; it just named the wrong reason.
TEST_COUNT = r"([\d,]{3,6}) tests"

DOCS = Path("docs")
DECISIONS = DOCS / "architecture" / "decisions"


# --------------------------------------------------------------------------- the wiring


def test_everything_the_container_declares_is_built(container):
    """A field that is None is a service the rest of the system will reach for and not find,
    at the moment it needs it rather than at startup."""
    unbuilt = [
        field.name
        for field in dataclasses.fields(container)
        if getattr(container, field.name) is None
    ]
    assert unbuilt == [], f"declared and never built: {unbuilt}"


def test_every_port_has_an_offline_adapter(container):
    """ADR 0001, and the reason the whole suite runs with no infrastructure: a system whose
    safety properties can only be tested against production is one whose safety properties
    are not tested."""
    from thursday_core.config import Settings
    from thursday_core.container import build_container

    offline = build_container(
        Settings(llm_backend="rule", vault_backend="memory", obsidian_enabled=False),
        configure_logs=False,
    )
    for name in ("models", "memory", "vault", "hub", "tools", "agents", "metrics"):
        assert getattr(offline, name) is not None, name


def test_every_action_in_the_catalogue_has_a_policy():
    """An action the policy table does not recognise falls to the fail-closed default, which
    is safe and useless: it asks about everything, and approval fatigue is a safety failure
    of its own."""
    table = PolicyTable()
    unrecognised = []
    for action in CATALOGUE:
        policy = table.get(action)
        namespace = action.split(".")[0]
        # The fail-closed default is exactly this shape. A namespace default is not.
        looks_defaulted = (
            policy.default is PolicyDecision.ASK_ALWAYS
            and policy.risk.value == "MEDIUM"
            and namespace not in _ASK_ALWAYS_NAMESPACES
        )
        if looks_defaulted:
            unrecognised.append(action)
    assert unrecognised == [], f"no policy of their own: {unrecognised}"


_ASK_ALWAYS_NAMESPACES = {
    "system",
    "email",
    "message",
    "social",
    "purchase",
    "payment",
    "shell",
    "script",
    "powershell",
    "code",
}


def test_every_agent_declares_what_it_returns(container):
    """V9 added `output_schema` because the orchestrator guessed a schema from a step's
    arguments and checked one agent's output against another agent's contract. Two agents
    were still relying on that guess when this test was written."""
    undeclared = [spec.name for spec in container.agents.specs() if not spec.output_schema]
    assert undeclared == [], f"no declared output schema: {undeclared}"


def test_every_agent_says_what_it_is_for(container):
    """The description is what the router selects on. An agent without one is unreachable by
    anything except its own name."""
    for spec in container.agents.specs():
        assert spec.description, spec.name
        assert spec.capabilities, spec.name


def test_a_tool_without_arguments_declares_that_deliberately(container):
    """Four tools have an empty input schema. That is correct — they take no arguments — and
    this test exists so the next empty one is a decision rather than an omission."""
    argumentless = {spec.name for spec in container.tools.specs() if not spec.input_schema}
    assert argumentless == {"screen.capture", "window.active", "system.info", "clock.now"}


# --------------------------------------------------------------------------- the documents


def test_every_adr_is_in_the_index():
    """An ADR nobody can find is a decision nobody knows was made."""
    index = (DECISIONS / "README.md").read_text(encoding="utf-8")
    missing = [p.name for p in sorted(DECISIONS.glob("0*.md")) if p.name not in index]
    assert missing == [], f"written and not indexed: {missing}"


def test_adrs_are_numbered_without_gaps_or_duplicates():
    numbers = sorted(int(p.name[:4]) for p in DECISIONS.glob("0*.md"))
    assert numbers == list(range(1, len(numbers) + 1)), numbers


def test_every_internal_link_points_at_something_that_exists():
    """A broken link in a design document is how a reader learns the documents are stale."""
    broken: list[str] = []
    for markdown in [Path("README.md"), *DOCS.rglob("*.md")]:
        text = markdown.read_text(encoding="utf-8")
        for match in re.finditer(r"\[[^\]]+\]\(([^)#]+?)(?:#[^)]*)?\)", text):
            target = match.group(1)
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            if not (markdown.parent / target).resolve().exists():
                broken.append(f"{markdown}: {target}")
    assert broken == [], broken


def test_the_readme_states_one_test_count_and_the_right_number_of_adrs():
    """The counts drifted twice during development — the README claimed 534 tests and seven
    phases while the branch carried 762 and ten. A number in a README is a claim, and this is
    the cheapest place to keep it honest."""
    readme = Path("README.md").read_text(encoding="utf-8")

    claimed = {int(n.replace(",", "")) for n in re.findall(TEST_COUNT, readme)}
    assert len(claimed) == 1, f"the README states more than one test count: {claimed}"

    # Generated rather than listed. The hand-written version ran out at thirty-six and then
    # matched "thirty" inside "thirty-seven", reporting the wrong number for a README that
    # was correct — a check that fails for its own reasons is worse than one that does not run.
    words = {
        f"{tens}{'-' + unit if unit else ''}": base + n
        for tens, base in (("twenty", 20), ("thirty", 30), ("forty", 40), ("fifty", 50))
        for n, unit in enumerate(
            ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
        )
    }
    # Longest first, and anchored: "thirty" is a prefix of "thirty-four", and matching the
    # short one made this test report 30 for a README that said thirty-four.
    stated = next(
        (
            n
            for word, n in sorted(words.items(), key=lambda kv: -len(kv[0]))
            if re.search(rf"\b{word}\b", readme)
        ),
        None,
    )
    actual = len(list(DECISIONS.glob("0*.md")))
    assert stated == actual, f"README says {stated} ADRs; there are {actual}"


def test_the_readme_has_not_fallen_behind_the_test_suite():
    """Counted statically, not from the run.

    The obvious version of this asks pytest how many tests it collected — and then passes or
    fails depending on whether you ran the whole suite or one file, which is not a test.

    So: every `def test_` in the tree is a lower bound on what pytest collects, because
    parametrisation only ever multiplies. If the README claims fewer than that, it has fallen
    behind — which is exactly the drift that happened twice here, both times understating.
    It does not catch an overstatement, and saying so is better than implying it does.
    """
    import ast

    functions = 0
    for path in Path("tests").rglob("test_*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        functions += sum(
            1
            for node in tree.body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name.startswith("test_")
        )

    readme = Path("README.md").read_text(encoding="utf-8")
    claimed = int(re.search(TEST_COUNT, readme).group(1).replace(",", ""))
    assert claimed >= functions, (
        f"README claims {claimed} tests; there are already {functions} test functions "
        "before parametrisation"
    )


# --------------------------------------------------------------------------- what is not ready


def test_the_unbuilt_parts_are_named_in_the_readme():
    """The honest half. Every gap this project knows about is written down where somebody
    evaluating it will look, and this test fails if one is quietly dropped from the list."""
    readme = Path("README.md").read_text(encoding="utf-8").lower()
    for gap in ("keychain", "mediapipe", "mobile"):
        assert gap in readme, f"a known gap is no longer stated: {gap}"


@pytest.mark.parametrize(
    "module",
    [
        "thursday_core.backup",
        "thursday_core.updates",
        "thursday_core.metrics",
        "thursday_core.cost",
        "thursday_security.pairing",
    ],
)
def test_the_late_sprint_modules_say_what_they_do_not_do(module):
    """Each of these ships with a stated limitation. A module that claims only what it
    achieves is one whose docstring can be trusted about the rest."""
    import importlib

    doc = (importlib.import_module(module).__doc__ or "").lower()
    assert len(doc) > 400, f"{module} has no real module docstring"
    assert any(
        phrase in doc
        for phrase in ("not ", "never", "cannot", "does not", "deliberately", "rather than")
    ), module
