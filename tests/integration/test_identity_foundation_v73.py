"""Identity, kept apart from permission (BIOMETRIC IDENTITY §83) — Sprint 73.

§1 is the claim the whole spec rests on:

    IDENTITY     who is giving the instruction
    PERMISSION   what that person may do
    APPROVAL     whether this action happens now
    "สามสิ่งนี้ห้ามรวมเป็นระบบเดียวกัน"

Systems merge these because merging looks tidier — one call, one verdict. What it produces is
a number that means neither thing: relaxing an identity threshold silently widens a permission,
and nobody can answer "what may a guest do" without also reasoning about cameras.

So the first tests here are structural, about what the Permission Engine did *not* gain.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from thursday_security.biometrics import (
    BiometricError,
    EnrolledTemplate,
    SecureBiometricStore,
)
from thursday_security.gate import ALWAYS_ALLOWED, CANNOT_VERIFY, IdentityGate
from thursday_security.identity import (
    ANONYMOUS,
    DEFAULT_MODE,
    FRESH_FOR,
    IDLE_BEFORE_DEGRADE,
    MAX_SESSION,
    MODE_FLOOR,
    REQUIRED_FOR_RISK,
    AuthContext,
    AuthenticationSession,
    AuthLevel,
    Factor,
    SecurityMode,
    UserKind,
    required_level,
)
from thursday_shared.enums import RiskLevel

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path) -> SecureBiometricStore:
    return SecureBiometricStore(directory=tmp_path / "biometrics")


def _session(**kw) -> AuthenticationSession:
    defaults = {
        "user_id": "owner",
        "kind": UserKind.OWNER,
        "auth_level": AuthLevel.SINGLE,
        "factors": {Factor.FACE},
        "started_at": NOW,
        "last_verified_at": NOW,
        "last_activity_at": NOW,
    }
    return AuthenticationSession(**{**defaults, **kw})


# ==================================================== §1 the three systems stay separate


def test_the_permission_engine_has_no_parameter_for_identity():
    """The structural expression of §1, and the thing to check if this is ever refactored.

    An engine that took an auth level would be one where relaxing an identity threshold
    widens a permission. The gate runs first instead, so an under-authenticated request never
    reaches the engine and the engine never has to be careful about one.
    """
    from thursday_security.permissions import PermissionEngine

    parameters = set(inspect.signature(PermissionEngine.decide).parameters)
    for identity in ("auth_level", "user_id", "identity", "session", "authenticated"):
        assert identity not in parameters


def test_the_action_request_carries_no_identity():
    """The other half: nothing about who is asking travels *inside* the object the engine
    judges. If it did, the separation would be one careless field access from gone."""
    from thursday_shared.models import ActionRequest

    fields = set(ActionRequest.model_fields)
    for identity in ("auth_level", "user_id", "identity", "face", "voice", "biometric"):
        assert identity not in fields


def test_the_gate_speaks_a_different_vocabulary_from_the_engine():
    """`GateVerdict` deliberately has no `PolicyDecision`. Sharing that vocabulary is how two
    systems start being treated as one."""
    from thursday_security.gate import GateVerdict

    annotations = {f: str(t) for f, t in GateVerdict.__annotations__.items()}
    assert not any("PolicyDecision" in t for t in annotations.values())
    assert "sufficient" in annotations


def test_the_gate_letting_something_through_is_not_permission(container):
    """A gate pass means "we know who is asking", never "they may". The engine can still
    refuse — and for a hard-blocked action it does, at any identity level."""
    gate = IdentityGate()
    verdict = gate.check(
        action="permission.self_grant", risk=RiskLevel.LOW, session=_session(), now=NOW
    )
    assert verdict.sufficient is True  # identity is fine

    from thursday_shared.models import ActionRequest

    decision = container.permissions.decide(ActionRequest(action="permission.self_grant"))
    assert decision.decision.value == "BLOCK"  # and the action is still refused


# ============================================================= §19/§20 levels and risk


def test_the_levels_are_ordered_so_a_floor_is_a_comparison():
    assert (
        AuthLevel.NONE
        < AuthLevel.SINGLE
        < AuthLevel.DEVICE_BACKED
        < AuthLevel.TWO_BIOMETRIC
        < AuthLevel.STRONG
    )


def test_risk_raises_the_identity_floor():
    """§20: Thursday does not ask for face and voice to open Chrome, and does ask for more
    before it deletes something."""
    floors = [required_level(risk) for risk in RiskLevel]
    assert floors == sorted(floors), "a higher risk must never need less identity"
    assert required_level(RiskLevel.CRITICAL) > required_level(RiskLevel.LOW)


@pytest.mark.parametrize("mode", list(SecurityMode))
def test_a_security_mode_can_only_ever_tighten(mode):
    """§58: security outranks convenience. A preset that *lowered* a floor would be a
    convenience setting that weakens security, so the composition is `max`, not a lookup."""
    for risk in RiskLevel:
        assert required_level(risk, mode=mode) >= REQUIRED_FOR_RISK[risk]
        assert required_level(risk, mode=mode) >= MODE_FLOOR[mode]


def test_maximum_mode_demands_the_strongest_identity_for_everything():
    for risk in RiskLevel:
        assert required_level(risk, mode=SecurityMode.MAXIMUM) is AuthLevel.STRONG


def test_owner_only_mode_needs_a_real_identity_even_for_trivia(container):
    """§22 means "nobody else uses this at all", not "nobody else does anything big"."""
    assert required_level(RiskLevel.LOW, owner_only=True) > AuthLevel.NONE

    gate = IdentityGate(owner_only=True)
    assert (
        gate.check(action="app.open", risk=RiskLevel.LOW, session=None, now=NOW).sufficient is False
    )


def test_the_shipped_default_mode_is_balanced():
    """§21 names it, and Sprint 62's lesson is that the shipped default is the product."""
    assert DEFAULT_MODE is SecurityMode.BALANCED


# ============================================================ §30/§40 refusals say nothing


def test_a_stranger_gets_one_sentence_and_no_detail():
    """§30 lists what must not leak: who the owner is, file names, counts, the schedule.
    §77's acceptance test is this sentence and nothing else."""
    verdict = IdentityGate().check(action="file.read", risk=RiskLevel.MEDIUM, session=None, now=NOW)
    assert verdict.sufficient is False
    assert verdict.message == CANNOT_VERIFY
    assert "owner" not in verdict.message.lower()


def test_a_refusal_never_carries_a_confidence(store):
    """§40: telling somebody "เสียงใกล้เคียง 83%" hands an attacker a gradient to climb."""
    verdict = IdentityGate().check(action="email.send", risk=RiskLevel.HIGH, session=None, now=NOW)
    text = verdict.message
    assert not any(ch.isdigit() for ch in text)
    assert "%" not in text


def test_every_refusal_uses_the_same_sentence():
    """One string, used everywhere, so no path can accidentally be more helpful — the leak
    here is always a well-meant extra clause."""
    gate = IdentityGate()
    messages = {
        gate.check(action=a, risk=r, session=s, now=NOW).message
        for a in ("file.read", "email.send", "file.delete")
        for r in (RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL)
        for s in (None, _session(kind=UserKind.GUEST))
    }
    assert messages == {CANNOT_VERIFY}


# ================================================================ §66 stopping is for all


@pytest.mark.parametrize("action", sorted(ALWAYS_ALLOWED))
def test_the_always_allowed_actions_need_no_identity(action):
    """§66. An emergency stop that required authentication would make an attacker's best
    move "cause identification to fail" — turning the safety control into another target."""
    verdict = IdentityGate(owner_only=True).check(
        action=action, risk=RiskLevel.CRITICAL, session=None, now=NOW
    )
    assert verdict.sufficient is True


def test_starting_things_again_is_not_on_that_list():
    """§66's distinction: stopping reduces risk, starting does not."""
    assert not any("start" in action for action in ALWAYS_ALLOWED)
    assert not any("resume" in action for action in ALWAYS_ALLOWED)


def test_everything_always_allowed_reduces_what_thursday_is_doing():
    for action in ALWAYS_ALLOWED:
        assert action.startswith(("system.stop", "system.emergency", "identity."))


# ==================================================================== §25/§26 sessions


def test_a_fresh_session_is_worth_what_it_was_established_at():
    session = _session(auth_level=AuthLevel.TWO_BIOMETRIC)
    assert session.effective_level(now=NOW) is AuthLevel.TWO_BIOMETRIC
    assert session.fresh(now=NOW) is True


def test_a_session_degrades_rather_than_vanishing():
    """§28's design. Stepping out for coffee should not mean re-enrolling, and must mean the
    session is no longer good enough to send an email."""
    session = _session(auth_level=AuthLevel.STRONG)
    later = NOW + FRESH_FOR + timedelta(minutes=1)
    assert session.effective_level(now=later) < AuthLevel.STRONG
    assert session.effective_level(now=later) > AuthLevel.NONE


def test_idleness_degrades_it_further():
    session = _session(auth_level=AuthLevel.STRONG)
    idle = NOW + FRESH_FOR + IDLE_BEFORE_DEGRADE + timedelta(minutes=1)
    assert session.effective_level(now=idle) <= AuthLevel.DEVICE_BACKED


def test_absence_ends_authentication_outright():
    """§28: somebody who is not there is not authenticated, whatever they were a moment ago.
    This is the case the whole presence system exists for — the owner walks away and somebody
    else sits down."""
    session = _session(auth_level=AuthLevel.STRONG)
    session.present = False
    assert session.effective_level(now=NOW) is AuthLevel.NONE
    assert session.fresh(now=NOW) is False


def test_a_session_ends_at_the_outer_bound_whatever_presence_says():
    """A presence signal that never fails is indistinguishable from one that is broken."""
    session = _session(auth_level=AuthLevel.STRONG)
    session.touch(now=NOW + MAX_SESSION)
    assert session.expired(now=NOW + MAX_SESSION) is True
    assert session.effective_level(now=NOW + MAX_SESSION) is AuthLevel.NONE


def test_activity_and_verification_are_different_facts():
    """Touching keeps a session from going idle; it does not re-establish who is there.
    Conflating them means typing is treated as proof of identity."""
    session = _session(auth_level=AuthLevel.STRONG)
    later = NOW + FRESH_FOR + timedelta(minutes=1)
    session.touch(now=later)
    assert session.last_activity_at == later
    assert session.last_verified_at == NOW
    assert session.effective_level(now=later) < AuthLevel.STRONG


def test_re_verifying_restores_the_level():
    session = _session(auth_level=AuthLevel.SINGLE)
    later = NOW + FRESH_FOR * 2
    session.verified(level=AuthLevel.STRONG, factors={Factor.VOICE}, now=later)
    assert session.effective_level(now=later) is AuthLevel.STRONG
    assert session.factors == {Factor.FACE, Factor.VOICE}


# =============================================== §59/§61 what an agent is allowed to see


def test_the_agent_context_is_exactly_the_four_fields_the_spec_allows():
    """§61 gives the shape verbatim. A distinct type, so handing an agent something richer
    means writing a different class rather than forgetting a field."""
    assert set(AuthContext.__annotations__) == {
        "authenticated",
        "user_id",
        "auth_level",
        "session_fresh",
    }
    assert set(_session().context(now=NOW).to_dict()) == {
        "authenticated",
        "user_id",
        "auth_level",
        "session_fresh",
    }


def test_no_biometric_word_appears_anywhere_in_what_an_agent_receives():
    """§59: an agent gets identity_id, auth_level and capabilities. Never a template, never a
    sample, never a confidence — an agent that knew the voice score could report it, and §40
    exists to stop exactly that reaching an attacker."""
    payload = str(_session(auth_level=AuthLevel.STRONG).context(now=NOW).to_dict()).lower()
    for forbidden in ("face", "voice", "template", "confidence", "liveness", "biometric"):
        assert forbidden not in payload


def test_an_unauthenticated_context_names_nobody():
    assert ANONYMOUS.authenticated is False
    assert ANONYMOUS.user_id is None
    assert ANONYMOUS.auth_level == 0


def test_a_degraded_session_stops_naming_the_owner():
    """Once the level falls to nothing, the context must not still be carrying who it was.
    An agent holding a user_id from a dead session is an agent acting as somebody."""
    session = _session(auth_level=AuthLevel.STRONG)
    session.present = False
    context = session.context(now=NOW)
    assert context.authenticated is False
    assert context.user_id is None


# ============================================================ §9/§10/§38 the template store


def test_a_stored_template_is_not_readable_from_disk(store, tmp_path):
    """The file on disk is not the secret. AES-GCM, so a tampered file fails to open rather
    than opening to something an attacker chose."""
    secret = b"THIS-IS-THE-FACE-TEMPLATE"
    store.store_template(user_id="owner", kind="face", template=secret, provider="test")

    files = list((tmp_path / "biometrics").glob("*.tpl"))
    assert files
    for path in files:
        assert secret not in path.read_bytes()

    assert store.load_template(user_id="owner", kind="face") == secret


def test_a_tampered_template_fails_closed(store, tmp_path):
    """The attack this stops is somebody swapping in their own face."""
    store.store_template(user_id="owner", kind="face", template=b"REAL", provider="test")
    path = next((tmp_path / "biometrics").glob("*.tpl"))
    body = bytearray(path.read_bytes())
    body[-1] ^= 0xFF
    path.write_bytes(bytes(body))

    with pytest.raises(BiometricError):
        store.load_template(user_id="owner", kind="face")


def test_the_store_has_no_way_to_export_everything(store):
    """§56: templates are not part of a normal export. There is no method that would put
    them in one, which is stronger than remembering to exclude them."""
    for forbidden in ("export", "export_all", "dump", "to_dict", "all_templates", "search"):
        assert not hasattr(store, forbidden), forbidden


def test_the_metadata_row_has_nowhere_to_put_a_raw_sample():
    """§7 and §8 delete the source images and audio once a template exists. A field to put
    them in is how that stops happening."""
    fields = set(EnrolledTemplate.__annotations__)
    for forbidden in ("raw", "sample", "image", "audio", "frames", "template"):
        assert forbidden not in fields


def test_the_store_repr_cannot_carry_a_template_into_a_traceback(store):
    """A container repr reaches tracebacks, debuggers and sometimes log lines."""
    store.store_template(user_id="owner", kind="face", template=b"SECRET", provider="test")
    assert "SECRET" not in repr(store)
    assert "SECRET" not in str(store)


def test_an_empty_template_is_refused(store):
    """A zero-length template would match nothing or everything depending on the provider,
    and either way it is not an enrolment."""
    with pytest.raises(BiometricError):
        store.store_template(user_id="owner", kind="face", template=b"", provider="test")


def test_deleting_a_template_removes_it_from_disk(store, tmp_path):
    """§57 — and "securely" starts with "actually"."""
    store.store_template(user_id="owner", kind="face", template=b"X", provider="test")
    assert store.delete_template(user_id="owner", kind="face") is True
    assert list((tmp_path / "biometrics").glob("*.tpl")) == []
    assert store.load_template(user_id="owner", kind="face") is None


def test_revoking_stops_a_template_being_used_without_destroying_it(store):
    """An owner who suspects something should be able to stop a template being used before
    deciding whether to re-enrol."""
    store.store_template(user_id="owner", kind="face", template=b"X", provider="test")
    assert store.revoke(user_id="owner", kind="face") is True
    assert store.load_template(user_id="owner", kind="face") is None
    assert store.enrolled(user_id="owner", kind="face") is False


def test_two_users_templates_do_not_collide(store):
    store.store_template(user_id="a", kind="face", template=b"AAA", provider="test")
    store.store_template(user_id="b", kind="face", template=b"BBB", provider="test")
    assert store.load_template(user_id="a", kind="face") == b"AAA"
    assert store.load_template(user_id="b", kind="face") == b"BBB"


def test_profiles_report_metadata_and_never_content(store):
    store.store_template(user_id="owner", kind="face", template=b"SECRET-BYTES", provider="p")
    rendered = str(list(store.profiles()))
    assert "SECRET-BYTES" not in rendered
    assert "owner" in rendered


def test_a_user_id_cannot_escape_the_store_directory(store, tmp_path):
    """The id reaches a filename. Without hashing it, `../../etc/passwd` is a write target."""
    store.store_template(user_id="../../escape", kind="face", template=b"X", provider="test")
    written = list((tmp_path / "biometrics").glob("**/*.tpl"))
    assert len(written) == 1
    assert written[0].parent == (tmp_path / "biometrics")


# ================================================================= §55 what is not built


def test_nothing_infers_an_attribute_about_a_person():
    """§55 forbids using any of this for ethnicity, religion, health, emotion, gender,
    politics or personality.

    Checked by collecting every *identifier* in the module — names, attributes, arguments,
    functions, classes, dict-literal string keys — rather than by scanning the text. The first
    version grepped the source and matched the module's own docstring saying it does not do
    this. That is the fifth time in this project a text scan has matched prose about the code
    instead of the code: Sprint 46's "curl", Sprint 52's docstring, Sprint 69's `.get()`,
    Sprint 70's `ActionRequest`, and now this. Prose is not code, and only one of the two is
    worth asserting on.
    """
    import ast

    from thursday_security import biometrics, gate, identity

    forbidden = {
        "ethnicity",
        "race",
        "gender",
        "sex",
        "emotion",
        "mood",
        "age_estimate",
        "religion",
        "politics",
        "personality",
        "health",
    }

    for module in (biometrics, identity, gate):
        tree = ast.parse(Path(module.__file__).read_text())
        identifiers: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                identifiers.add(node.id)
            elif isinstance(node, ast.Attribute):
                identifiers.add(node.attr)
            elif isinstance(node, ast.arg):
                identifiers.add(node.arg)
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                identifiers.add(node.name)
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                # A dict key or literal is how such a field would most plausibly appear.
                # Docstrings are Constants too, so only short strings count as identifiers.
                and len(node.value) < 40
            ):
                identifiers.add(node.value)

        lowered = {name.lower() for name in identifiers}
        leaked = {name for name in lowered if any(bad in name for bad in forbidden)}
        assert not leaked, f"{module.__name__} has identifiers for {leaked}"


# ============================================== the StrEnum comparison trap, guarded


@pytest.mark.parametrize("risk", [RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL])
def test_a_guest_cannot_take_a_serious_action_however_well_identified(risk):
    """§32, and the bug this test exists for.

    The first version wrote `risk > RiskLevel.LOW`. `RiskLevel` is a StrEnum, so that
    compares *strings*, and "HIGH" sorts below "LOW" alphabetically — meaning the clause
    meant to stop a guest deleting files let exactly HIGH and CRITICAL through while
    correctly blocking MEDIUM. The enum module warns about this in its own comment.

    A guest at the strongest possible authentication is still a guest: the bar they fail is
    who they are, not how well they proved it.
    """
    guest = _session(
        kind=UserKind.GUEST,
        auth_level=AuthLevel.STRONG,
        factors={Factor.FACE, Factor.VOICE, Factor.TRUSTED_DEVICE},
    )
    verdict = IdentityGate().check(action="file.delete", risk=risk, session=guest, now=NOW)
    assert verdict.sufficient is False
    assert verdict.message == CANNOT_VERIFY


def test_a_guest_may_still_do_harmless_things(container):
    """§32 allows a guest music, timers and weather. Locking them out entirely would make
    guest mode pointless."""
    guest = _session(kind=UserKind.GUEST, auth_level=AuthLevel.SINGLE)
    assert (
        IdentityGate()
        .check(action="app.open", risk=RiskLevel.LOW, session=guest, now=NOW)
        .sufficient
        is True
    )


def test_no_risk_comparison_in_the_identity_layer_uses_a_bare_operator():
    """The general form of the same bug. Every risk comparison must go through the ranked
    helpers; a bare `<` or `>` on a RiskLevel is the trap."""
    import ast

    from thursday_security import gate as gate_module

    tree = ast.parse(Path(gate_module.__file__).read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        rendered = ast.unparse(node)
        if "RiskLevel" not in rendered:
            continue
        assert not any(isinstance(op, ast.Lt | ast.Gt | ast.LtE | ast.GtE) for op in node.ops), (
            f"ordered comparison on a StrEnum: {rendered}"
        )
