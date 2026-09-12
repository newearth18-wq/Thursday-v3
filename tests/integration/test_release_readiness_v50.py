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
    # Asked of the table directly rather than inferred from the resolved policy's shape.
    #
    # This used to guess: a policy of ASK_ALWAYS/MEDIUM outside a known-strict namespace was
    # taken to be the fail-closed default. That worked until Sprint 60 added `device.wake`
    # with a *deliberate* ASK_ALWAYS/MEDIUM — indistinguishable from the default by shape, so
    # the check reported a policy that exists as missing. The heuristic was always standing in
    # for "is this action listed", and the table can answer that itself.
    listed = set(table.known_actions())
    unrecognised = [
        action
        for action in CATALOGUE
        if action not in listed and not _covered_by_ancestor(action, listed)
    ]
    assert unrecognised == [], f"no policy of their own: {unrecognised}"


def _covered_by_ancestor(action: str, listed: set[str]) -> bool:
    """Whether a listed ancestor governs this action (ADR 0007's prefix walk).

    `file.folder.create` is covered by `file.folder`, which is the resolution rule the
    engine itself uses — so an action inheriting a real policy is not missing one.
    """
    parts = action.split(".")
    return any(".".join(parts[:i]) in listed for i in range(len(parts) - 1, 0, -1))


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

    # Anchored to the sentence that is actually about ADRs. The unanchored version searched
    # the whole README and matched "twenty-four" from "the twenty-four deliverables" — a
    # number word about something else entirely. Generated rather than listed, because the
    # hand-written list ran out at thirty-six and then matched "thirty" inside "thirty-seven".
    words = {
        f"{tens}{'-' + unit if unit else ''}": base + n
        for tens, base in (
            ("twenty", 20),
            ("thirty", 30),
            ("forty", 40),
            ("fifty", 50),
            # The generator stopped at fifty-nine and the sixtieth ADR made `stated` None,
            # so this failed with "README says None ADRs" rather than with a wrong number.
            # Extended well past the count rather than by one, which is the same mistake in
            # a smaller size — the previous hand-written list ran out at thirty-six.
            ("sixty", 60),
            ("seventy", 70),
            ("eighty", 80),
            ("ninety", 90),
        )
        for n, unit in enumerate(
            ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
        )
    }
    sentence = re.search(r"and ([a-z\-]+)\s*\n?\[architecture decisions\]", readme)
    assert sentence, "the README no longer states an ADR count where this test looks for it"
    stated = words.get(sentence.group(1))
    assert stated is not None, (
        f"the README says {sentence.group(1)!r} ADRs and this test cannot read that as a "
        "number — extend `words` rather than assuming the count is wrong"
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


async def test_the_readme_does_not_overstate_the_api(settings, container):
    """The README claimed 80 REST operations while the app served 126 — a number written
    once, never checked, and wrong in the flattering direction by the time anyone read it.

    Counted from the running application rather than from the router files, because the
    question a reader is asking is what the server actually exposes.
    """
    from thursday_api.app import create_app

    app = create_app(settings, container=container)
    app.state.container = container
    spec = app.openapi()
    served = sum(
        len([m for m in methods if m in ("get", "post", "put", "patch", "delete")])
        for methods in spec["paths"].values()
    )

    readme = Path("README.md").read_text(encoding="utf-8")
    match = re.search(r"(\d+) REST operations", readme)
    assert match, "the README no longer states a REST operation count"
    assert int(match.group(1)) == served, (
        f"README says {match.group(1)} REST operations; the app serves {served}"
    )


def test_the_readme_does_not_overstate_the_desktop_suite():
    """The README said 62 desktop tests while the app had 155 — the same drift the Python
    count test was written for, in the window nothing was counting.

    Counted statically, like its Python sibling: every `it(` or `test(` in a spec file is
    what vitest collects, so the README claiming more than that is a claim nothing backs.
    """
    specs = [
        *Path("apps/desktop/src").rglob("*.test.ts"),
        *Path("apps/desktop/src").rglob("*.test.tsx"),
    ]
    assert specs, "the desktop suite has moved; this test is looking in the wrong place"
    written = sum(
        len(re.findall(r"^\s*(?:it|test)\(", spec.read_text(encoding="utf-8"), re.MULTILINE))
        for spec in specs
    )

    readme = Path("README.md").read_text(encoding="utf-8")
    match = re.search(r"plus ([\d,]+) in the desktop app", readme)
    assert match, "the README no longer states a desktop test count"
    claimed = int(match.group(1).replace(",", ""))
    assert claimed <= written, f"README claims {claimed} desktop tests; {written} are written"


# -------------------------------------------------- the documents that describe the bench

#: Number words the three documents use for the size of the agent bench. Same generator
#: idea as the ADR count above, and the same reason: a hand-written list runs out.
_TENS = (("twenty", 20), ("thirty", 30), ("forty", 40), ("fifty", 50))
_UNITS = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
NUMBER_WORDS: dict[str, int] = {
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    **{
        f"{tens}{'-' + unit if unit else ''}": base + n
        for tens, base in _TENS
        for n, unit in enumerate(_UNITS)
    },
}


def _registered(container) -> dict[str, str]:
    return {spec.name: spec.permission_ceiling.name for spec in container.agents.specs()}


def test_the_agent_bench_table_lists_every_agent_that_actually_runs(container):
    """§21's table said thirteen while the container registered eighteen: `teacher`,
    `library`, `event`, `trading` and `tutor` were absent from the document describing the
    bench. A reader counting agents from the docs was five short."""
    bench = Path("docs/21-agents-and-skills.md").read_text(encoding="utf-8")
    listed = set(re.findall(r"^\| `([a-z_]+)` \|", bench, re.MULTILINE))
    assert listed == set(_registered(container)), (
        f"in the table and not registered: {sorted(listed - set(_registered(container)))}; "
        f"registered and not in the table: {sorted(set(_registered(container)) - listed)}"
    )


def test_the_bench_table_states_each_agents_real_ceiling(container):
    """The row that went stale first was a **ceiling**, not a description: `media` was
    listed READ and "Cannot edit them" long after V11 gave it editing tools and MODIFY.

    A document that understates what an agent may do is worse than one that is merely out
    of date — it is the document somebody reads to decide whether to trust the machine.
    """
    bench = Path("docs/21-agents-and-skills.md").read_text(encoding="utf-8")
    rows = dict(re.findall(r"^\| `([a-z_]+)` \| .* \| ([A-Z]+) \|$", bench, re.MULTILINE))
    assert rows == _registered(container), (
        f"documented {rows} vs registered {_registered(container)}"
    )


@pytest.mark.parametrize(
    ("document", "pattern"),
    [
        ("README.md", r"- ([A-Za-z\-]+) specialist agents plus the Supervisor"),
        ("docs/21-agents-and-skills.md", r"^([A-Za-z\-]+) specialists plus the Supervisor"),
        ("docs/23-release-readiness.md", r"\| ([A-Za-z\-]+) agents; skills learned"),
    ],
)
def test_every_document_that_counts_the_agents_counts_the_same_number(container, document, pattern):
    """ "Thirteen" was written in three places and wrong in all three. One registry, one
    number, and nothing left that can disagree with it quietly."""
    text = Path(document).read_text(encoding="utf-8")
    match = re.search(pattern, text, re.MULTILINE)
    assert match, f"{document} no longer states an agent count where this test looks"
    stated = NUMBER_WORDS.get(match.group(1).lower())
    assert stated is not None, (
        f"{document} says {match.group(1)!r} agents and this test cannot read that as a "
        "number — extend `NUMBER_WORDS` rather than assuming the count is wrong"
    )
    assert stated == len(_registered(container)), (
        f"{document} says {stated} agents; {len(_registered(container))} are registered"
    )


def test_the_readiness_document_agrees_with_the_readme_about_the_suite():
    """Both state a test count. The README's is checked against the tree above, so making
    them agree makes both true — and §23 had drifted 159 tests behind."""
    readme = re.search(TEST_COUNT, Path("README.md").read_text(encoding="utf-8"))
    readiness = re.search(
        TEST_COUNT, Path("docs/23-release-readiness.md").read_text(encoding="utf-8")
    )
    assert readme and readiness, "one of the two no longer states a test count"
    assert readme.group(1) == readiness.group(1), (
        f"README says {readme.group(1)} tests; §23 says {readiness.group(1)}"
    )


def test_the_readiness_document_does_not_deny_a_capability_thursday_has(container):
    """§23 exists to be honest about gaps, so a gap it names that has since been closed is
    the one kind of error it cannot afford.

    It claimed "no text-to-speech" for four sprints after V12 gave Thursday a voice —
    twenty lines below its own paragraph describing that voice.
    """
    text = Path("docs/23-release-readiness.md").read_text(encoding="utf-8")
    denials = {
        "no text-to-speech": container.narrator is not None,
        "no automation engine": container.automations is not None,
        "no trading module": container.agents.has("trading"),
    }
    # The sentence recording what the document used to say is allowed to quote the old
    # wording; only a live claim counts, so the quoted phrase is removed before looking.
    live = text.replace('"and no text-to-speech"', "")
    wrong = [phrase for phrase, present in denials.items() if present and phrase in live]
    assert wrong == [], f"§23 denies a capability Thursday has: {wrong}"
