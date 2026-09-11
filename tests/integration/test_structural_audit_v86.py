"""Rules that hold across the whole repository, from the Sprint 86 audit.

Two of this project's defects were found by reading one file carefully and then wondering
how many others had the same shape. Both answers were "more than one", so both checks are
here as walks over every module rather than as assertions about the place they were found.

Nothing here needs the app to be running. They are structural claims, and a structural
claim is worth having only if it fails when broken — each is paired with a note saying what
the break looked like when it was real.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIRS = ("packages", "services", "apps/node")
SKIP_PARTS = {"__pycache__", ".venv", "node_modules", "gen"}

ORDERED = (ast.Lt, ast.Gt, ast.LtE, ast.GtE)


def _modules() -> list[Path]:
    found: list[Path] = []
    for directory in SOURCE_DIRS:
        for path in (ROOT / directory).rglob("*.py"):
            if not any(part in SKIP_PARTS for part in path.parts):
                found.append(path)
    assert len(found) > 100, "the audit is not finding the source tree"
    return found


def _str_enums() -> dict[str, Path]:
    """Every StrEnum in the repository, by name."""
    names: dict[str, Path] = {}
    for path in _modules():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases = {
                    base.id if isinstance(base, ast.Name) else getattr(base, "attr", "")
                    for base in node.bases
                }
                if "StrEnum" in bases:
                    names[node.name] = path
    return names


# ------------------------------------------------- the StrEnum comparison trap, repo-wide


def test_no_str_enum_is_ever_compared_with_an_ordered_operator():
    """The general form of the bug that let a guest delete files.

    `RiskLevel` is a StrEnum, so `risk > RiskLevel.LOW` compares *strings*: "HIGH" sorts
    below "LOW", and the clause meant to stop a guest taking serious actions let exactly
    HIGH and CRITICAL through while correctly blocking MEDIUM
    (`test_identity_foundation_v73.py` has the story).

    That was caught in `thursday_security.gate`, and the guard written for it walks that one
    module and looks for that one enum. There are forty-odd StrEnums here and the trap is
    identical in every one of them, so this walks all of them. Ordering a StrEnum is never
    what somebody meant: where an order genuinely exists the enum is an `IntEnum`
    (`AuthLevel`, `PermissionLevel`, `TrustLevel`) or there is a ranked helper
    (`risk_at_least`, `risk_rank`, `max_risk`).
    """
    enums = _str_enums()
    assert "RiskLevel" in enums, "the audit is not seeing the enum module"

    offences: list[str] = []
    for path in _modules():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            if not any(isinstance(op, ORDERED) for op in node.ops):
                continue
            rendered = ast.unparse(node)
            for name in enums:
                # `Enum.MEMBER` appearing anywhere in an ordered comparison
                if f"{name}." in rendered:
                    offences.append(f"{path.relative_to(ROOT)}: {rendered}")
                    break

    assert not offences, "ordered comparison on a StrEnum:\n  " + "\n  ".join(offences)


def test_the_enums_that_are_meant_to_be_ordered_are_int_enums():
    """The other half: this rule is only liveable because ranking has somewhere to live.

    If an authorisation level were a StrEnum, the rule above would be telling people not to
    do the thing they need to do, and the rule would lose.
    """
    from thursday_shared.enums import AutonomyLevel, PermissionLevel, RiskLevel

    assert AutonomyLevel.HIGH > AutonomyLevel.SUGGEST_ONLY
    assert PermissionLevel.ADMIN > PermissionLevel.READ
    assert isinstance(RiskLevel.HIGH, str)
    # And the documented trap really is a trap, so the rule above is not theatre.
    assert RiskLevel.HIGH < RiskLevel.LOW, "the string ordering this rule exists for changed"


# ------------------------------------------------------ collections that only ever grow


def test_the_hot_path_singletons_do_not_grow_without_bound():
    """The `_seen` bug class, pinned to the objects where it actually mattered.

    Deliberately named rather than a repo-wide sweep: the sweep that found these returned
    thirty-nine hits, most of them test doubles and small domain collections, and a test
    that needs a hand-maintained allowlist of exceptions rots into noise. These four live
    for the life of the process and are written to on every event, every voice transition
    and every model measurement.
    """
    import inspect

    from thursday_core.benchmarks import BenchmarkProfile
    from thursday_core.bus import InProcessEventBus
    from thursday_voice.state import VoiceStateMachine

    bus_source = inspect.getsource(InProcessEventBus)
    assert "_dedupe_limit" in bus_source, "the replay guard lost its bound"
    assert "self._history_limit" in bus_source

    machine_source = inspect.getsource(VoiceStateMachine)
    assert "maxlen" in machine_source, "voice transition history lost its bound"

    profile_source = inspect.getsource(BenchmarkProfile)
    assert "maxlen" in profile_source, "benchmark samples lost their bound"
