"""Putting the evidence together (BIOMETRIC IDENTITY §86) — Sprint 76.

§90 is the warning this sprint implements:

    Thursday ต้อง "รู้จักเจ้าของ" แต่ไม่ควรเชื่อเพียงเพราะ
    เห็นหน้าคล้าย · ได้ยินเสียงคล้าย · อยู่บนเครื่องเดิม

Each factor alone is a resemblance an attacker can arrange, and each is individually
convincing. So the level counts *independent kinds* of evidence and never sums confidences —
a 0.99 face and a 0.99 voice are the same two things more confidently, not more independent
things, and summing would let one excellent factor buy what two mediocre independent ones
could not. One excellent factor is one photograph away from wrong.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
from thursday_security import fusion as fusion_module
from thursday_security.fusion import (
    DEVICE_ONLY_CEILING,
    USABLE_CONFIDENCE,
    DeviceTrust,
    IdentityFusionEngine,
)
from thursday_security.identity import AuthLevel, Factor, IdentityClaim, UserKind
from thursday_security.voice import VOICE_ALONE_CEILING


@pytest.fixture
def engine() -> IdentityFusionEngine:
    return IdentityFusionEngine()


def _face(user="owner", confidence=0.95, liveness=0.9) -> IdentityClaim:
    return IdentityClaim(factor=Factor.FACE, user_id=user, confidence=confidence, liveness=liveness)


def _voice(user="owner", confidence=0.95, liveness=0.9) -> IdentityClaim:
    return IdentityClaim(
        factor=Factor.VOICE, user_id=user, confidence=confidence, liveness=liveness
    )


# ================================================== §17 the voice ceiling is enforced here


def test_voice_alone_never_passes_its_ceiling_however_confident(engine):
    """Sprint 75 declared the constant; this is where it binds. A cloned voice matches
    perfectly by construction, so confidence is not the thing that should move this."""
    fused = engine.fuse([_voice(confidence=1.0, liveness=1.0)])
    assert fused.identified
    assert fused.level <= VOICE_ALONE_CEILING


def test_voice_plus_a_trusted_device_is_still_capped(engine):
    """The ceiling is on *voice as the only biometric*, not on voice as the only factor.
    A recording played near the owner's own unlocked laptop has both."""
    fused = engine.fuse([_voice(confidence=1.0)], device_trusted=True)
    assert fused.level <= VOICE_ALONE_CEILING
    assert any("voice alone" in r for r in fused.reasons)


def test_the_ceiling_is_imported_rather_than_redefined():
    """A ceiling this module could compute is one it could raise. Changing it means editing
    the file where §17 is quoted."""
    source = Path(fusion_module.__file__).read_text()
    assert "from thursday_security.voice import VOICE_ALONE_CEILING" in source

    tree = ast.parse(source)
    assigned = {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert "VOICE_ALONE_CEILING" not in assigned


def test_face_and_voice_together_pass_the_voice_ceiling(engine):
    """Two independent biometrics are a different thing from a loud voice."""
    fused = engine.fuse([_face(), _voice()])
    assert fused.level >= AuthLevel.TWO_BIOMETRIC


# ==================================================== §36 possession is not identity


def test_a_trusted_device_alone_does_not_establish_a_person(engine):
    """Somebody holding the owner's unlocked laptop has device trust and is not the owner."""
    fused = engine.fuse([], device_trusted=True)
    assert fused.identified is False


def test_a_device_never_reaches_a_level_that_needs_a_person(engine):
    fused = engine.fuse(
        [IdentityClaim(factor=Factor.PIN, user_id="owner", confidence=1.0)],
        device_trusted=True,
        os_authenticated=True,
    )
    assert fused.level <= DEVICE_ONLY_CEILING


def test_device_trust_needs_more_than_being_on_the_usual_wifi():
    """ "Known network" is true of everyone in the building. Pairing plus a valid credential
    is the bar; the softer signals adjust confidence and never substitute for it."""
    assert DeviceTrust(known_network=True, os_unlocked=True).trusted is False
    assert DeviceTrust(paired=True).trusted is False
    assert DeviceTrust(paired=True, credential_valid=True).trusted is True


def test_device_trust_carries_nothing_about_a_person():
    """The type is the guarantee: "the laptop is trusted" cannot quietly become "the owner
    is here" if there is no field where a person could be recorded."""
    fields = set(DeviceTrust.__annotations__)
    for personal in ("user_id", "face", "voice", "owner", "identity", "confidence"):
        assert personal not in fields


# ================================================ §12/§13 no liveness contributes nothing


def test_a_face_with_no_liveness_is_not_evidence_of_anybody(engine):
    """The photograph, at the fusion boundary as well as inside the matcher. Belt and braces
    on purpose: this is the join where a future provider that forgot to check liveness would
    otherwise be believed."""
    fused = engine.fuse([_face(confidence=1.0, liveness=0.0)])
    assert fused.identified is False
    assert any("living" in r for r in fused.reasons)


def test_a_photograph_plus_a_recording_still_identifies_nobody(engine):
    """Two dead factors are not two factors. This is the whole attack: hold up a printout,
    play a recording, and both match perfectly."""
    fused = engine.fuse([_face(confidence=1.0, liveness=0.0), _voice(confidence=1.0, liveness=0.0)])
    assert fused.level is AuthLevel.NONE


def test_a_weak_match_is_not_evidence(engine):
    fused = engine.fuse([_face(confidence=USABLE_CONFIDENCE - 0.01)])
    assert fused.identified is False


def test_liveness_is_only_demanded_of_biometrics(engine):
    """A PIN has no liveness and does not need one — the concept does not apply. Demanding
    it would silently discard every non-biometric factor."""
    fused = engine.fuse(
        [
            _face(),
            IdentityClaim(factor=Factor.PIN, user_id="owner", confidence=1.0, liveness=0.0),
        ]
    )
    assert fused.identified is True
    assert fused.level >= AuthLevel.DEVICE_BACKED


# ======================================================== §64 factors that disagree


def test_two_factors_naming_two_people_identify_nobody(engine):
    """§64's scenario: an unknown face at the keyboard playing a recording of the owner.
    The honest answer is that nobody is established — not that the majority wins, and not
    that the more confident one does."""
    fused = engine.fuse([_face(user="stranger"), _voice(user="owner", confidence=1.0)])
    assert fused.identified is False
    assert fused.level is AuthLevel.NONE
    assert any("different people" in r for r in fused.reasons)


def test_disagreement_beats_confidence(engine):
    """Even when one side is overwhelming. A very confident voice and a mismatched face is
    exactly the shape of a replay attack against somebody sitting there."""
    fused = engine.fuse(
        [_face(user="stranger", confidence=0.6), _voice(user="owner", confidence=1.0)]
    )
    assert fused.identified is False


# ============================================== counting kinds rather than summing scores


def test_confidence_never_buys_a_level(engine):
    """The central rule. Two mediocre independent factors outrank one perfect one."""
    perfect_single = engine.fuse([_face(confidence=1.0, liveness=1.0)])
    two_mediocre = engine.fuse(
        [_face(confidence=0.6, liveness=0.7), _voice(confidence=0.6, liveness=0.7)]
    )
    assert two_mediocre.level > perfect_single.level


def test_nothing_in_the_engine_sums_or_averages_confidences():
    """Structural. A weighted sum is how "how many things must be defeated" quietly becomes
    "how sure is the best one"."""
    source = Path(fusion_module.__file__).read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"sum", "mean", "fmean", "average"}
    assert "confidence +" not in source
    assert "+ claim.confidence" not in source


def test_the_strongest_level_needs_two_biometrics_and_a_device(engine):
    """§19's AUTH_LEVEL_4, and §65's worked example."""
    fused = engine.fuse([_face(), _voice()], device_trusted=True)
    assert fused.level is AuthLevel.STRONG
    assert fused.next_factor is None


def test_a_biometric_plus_a_device_sits_between_the_two(engine):
    fused = engine.fuse([_face()], device_trusted=True)
    assert fused.level is AuthLevel.DEVICE_BACKED


# =========================================================== §18 required_next_factor


def test_the_next_factor_adds_a_kind_rather_than_more_of_the_same(engine):
    """Asking a voice-only session to speak again would not move the level, and asking for
    something it cannot supply is how a system trains people to give up."""
    assert engine.fuse([_voice()]).next_factor is Factor.FACE
    assert engine.fuse([_face()]).next_factor is Factor.VOICE
    assert engine.fuse([_face(), _voice()]).next_factor is Factor.OS_BIOMETRIC


def test_nothing_further_is_asked_for_at_the_top(engine):
    assert engine.fuse([_face(), _voice()], device_trusted=True).next_factor is None


def test_somebody_unidentified_is_asked_for_a_face(engine):
    fused = engine.fuse([])
    assert fused.identified is False
    assert fused.next_factor is Factor.FACE


# ===================================================================== the boundary


def test_fusion_holds_no_policy_about_actions():
    """§1 again. The engine answers "who, and how well established" — the gate compares that
    against what an action needs, and mixing the two is how identity becomes permission."""
    parameters = set(inspect.signature(IdentityFusionEngine.fuse).parameters)
    for policy in ("action", "risk", "resource", "permission", "mode"):
        assert policy not in parameters


def test_the_result_carries_no_confidence_out(engine):
    """§40. What leaves this layer is a level, which is a decision — not a measurement an
    attacker could use as a gradient."""
    fused = engine.fuse([_face(), _voice()])
    fields = set(type(fused).__annotations__)
    for measurement in ("confidence", "liveness", "score", "similarity"):
        assert measurement not in fields


def test_a_guest_is_identified_and_noted_as_one(engine):
    """Level is about how well they were established; the gate refuses them serious actions
    by kind. Both facts have to survive fusion."""
    fused = engine.fuse([_face(), _voice()], kind=UserKind.GUEST)
    assert fused.identified is True
    assert any("guest" in r for r in fused.reasons)
