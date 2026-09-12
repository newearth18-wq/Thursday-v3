"""The core's TLS key can change without a visit to every machine (§117, ADR 0071).

§23 has carried the same sentence for several sprints: device keys rotate, the enrolment
token rotates, provider keys rotate, and the core's TLS key does not — because a node pins
the core's SubjectPublicKeyInfo and replacing it locks every node out at the same instant.

The way through is that a pin is a *commitment to a public key*, so a statement carrying the
retiring key can be checked against nothing but the pin, and then used to verify a signature
by that key's private half. These tests are about whether that actually holds, including for
the kinds of key an operator's certificate really has — which is not ours to choose.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
from thursday_security.handover import (
    CHAIN_MAX,
    HandOver,
    HandOverInvalid,
    HandOverNotMine,
    HandOverRefused,
    follow,
    sign,
    verify,
)
from thursday_security.pinning import pin_for_spki

from tests.integration.test_pinning import make_certificate


def generate(kind: str):
    if kind == "rsa":
        return rsa.generate_private_key(public_exponent=65537, key_size=2048)
    if kind == "ec":
        return ec.generate_private_key(ec.SECP256R1())
    return ed25519.Ed25519PrivateKey.generate()


def pem(key) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("utf-8")


def pin_of(key) -> str:
    return pin_for_spki(
        key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )


#: The three kinds a TLS certificate in the wild actually uses. Parametrised rather than
#: picked, because unlike a device identity this key belongs to the operator's CA or proxy
#: and refusing RSA would mean refusing most real deployments.
KINDS = ["rsa", "ec", "ed25519"]


# ------------------------------------------------------------------ the statement verifies


@pytest.mark.parametrize("kind", KINDS)
def test_a_node_holding_only_a_pin_can_verify_the_key_behind_it(kind):
    """The whole mechanism in one assertion.

    The node stores `sha256(SPKI)` and nothing else. The hand-over carries the SPKI, so the
    node can confirm the hash matches and *then* use the key it now holds to check a
    signature made by the private half. No new anchor, no re-pairing.
    """
    retiring = generate(kind)
    incoming = generate(kind)

    handover = sign(pem(retiring), next_pin=pin_of(incoming), compromised=False)

    assert handover.retiring_pin == pin_of(retiring)
    verify(handover, pin_of(retiring))  # does not raise


@pytest.mark.parametrize("kind", KINDS)
def test_a_hand_over_pointed_at_a_different_successor_stops_verifying(kind):
    """`next_pin` is inside the signature, which is the point of putting it there.

    Without it a captured hand-over could be re-pointed at a key of the attacker's choosing
    while keeping the core's signature — the same attack `rotation_payload` names when it
    explains why `new_public_key` is signed.
    """
    retiring, incoming, attacker = generate(kind), generate(kind), generate(kind)
    handover = sign(pem(retiring), next_pin=pin_of(incoming), compromised=False)

    tampered = replace(handover, next_pin=pin_of(attacker))

    with pytest.raises(HandOverInvalid):
        verify(tampered, pin_of(retiring))


def test_a_hand_over_signed_by_some_other_key_is_not_this_nodes_business():
    retiring, stranger, incoming = generate("ec"), generate("ec"), generate("ec")
    handover = sign(pem(stranger), next_pin=pin_of(incoming), compromised=False)

    with pytest.raises(HandOverNotMine):
        verify(handover, pin_of(retiring))


def test_a_supplied_retiring_pin_is_ignored_when_parsing():
    """`to_dict` writes the pin for a human reading the file; `from_dict` never reads it.

    A pin carried beside the key it claims to be a hash of is a field a forger would like the
    parser to believe instead of computing.
    """
    retiring, incoming = generate("ed25519"), generate("ed25519")
    raw = sign(pem(retiring), next_pin=pin_of(incoming), compromised=False).to_dict()
    raw["retiring_pin"] = "AAAA-not-the-pin-of-anything"

    assert HandOver.from_dict(raw).retiring_pin == pin_of(retiring)


def test_a_hand_over_cannot_be_written_for_a_key_the_signer_does_not_hold():
    """`sign` takes no retiring-SPKI argument, and that absence is the property.

    Offering one would make "a hand-over naming a key I do not have" an ordinary call rather
    than a forgery somebody has to work at.
    """
    import inspect

    assert "spki" not in inspect.signature(sign).parameters


# ------------------------------------------------------------- what a hand-over cannot fix


def test_signing_is_refused_when_the_retiring_key_was_compromised():
    """The sharp edge of the thing that makes following safe.

    A node follows a hand-over because the retiring key could have impersonated the core
    anyway. Turn that around: if somebody else holds that key, they can sign a hand-over too,
    and each node follows whichever reaches it first. Rotating away from a stolen key is a
    race, so a hand-over that reported success would tell the owner their machines were moved
    to safety when some of them may have been moved somewhere else.
    """
    retiring, incoming = generate("rsa"), generate("rsa")

    with pytest.raises(HandOverRefused) as caught:
        sign(pem(retiring), next_pin=pin_of(incoming), compromised=True)

    assert "re-pair" in str(caught.value).lower()


def test_the_caller_has_to_answer_whether_the_key_was_compromised():
    """`compromised` has no default. A question with a default answer is not asked."""
    import inspect

    parameter = inspect.signature(sign).parameters["compromised"]
    assert parameter.default is inspect.Parameter.empty


# --------------------------------------------------------------------------- walking a chain


def test_a_node_that_missed_two_rotations_walks_to_the_current_key():
    a, b, c = generate("ec"), generate("ec"), generate("ec")
    chain = [
        sign(pem(a), next_pin=pin_of(b), compromised=False),
        sign(pem(b), next_pin=pin_of(c), compromised=False),
    ]

    walked = follow(chain, pin_of(a))

    assert [link.next_pin for link in walked] == [pin_of(b), pin_of(c)]


def test_a_node_that_missed_only_the_last_one_starts_where_it_is():
    """Starting from the top would mean accepting a first link this node never trusted."""
    a, b, c = generate("ec"), generate("ec"), generate("ec")
    chain = [
        sign(pem(a), next_pin=pin_of(b), compromised=False),
        sign(pem(b), next_pin=pin_of(c), compromised=False),
    ]

    walked = follow(chain, pin_of(b))

    assert [link.next_pin for link in walked] == [pin_of(c)]


def test_a_node_whose_pin_is_nowhere_in_the_chain_is_told_so():
    a, b, stranger = generate("ec"), generate("ec"), generate("ec")
    chain = [sign(pem(a), next_pin=pin_of(b), compromised=False)]

    with pytest.raises(HandOverNotMine):
        follow(chain, pin_of(stranger))


def test_one_forged_link_stops_the_walk_rather_than_being_skipped():
    """A chain is only as good as every link, and a walk that stepped over a bad one would
    hand the node whatever came after it."""
    a, b, c, attacker = (generate("ed25519") for _ in range(4))
    good = sign(pem(a), next_pin=pin_of(b), compromised=False)
    forged = replace(sign(pem(b), next_pin=pin_of(c), compromised=False), next_pin=pin_of(attacker))

    with pytest.raises(HandOverInvalid):
        follow([good, forged], pin_of(a))


def test_a_chain_that_loops_is_refused():
    a, b = generate("ed25519"), generate("ed25519")
    chain = [
        sign(pem(a), next_pin=pin_of(b), compromised=False),
        sign(pem(b), next_pin=pin_of(a), compromised=False),
    ]

    with pytest.raises(HandOverInvalid) as caught:
        follow(chain, pin_of(a))

    assert "loop" in str(caught.value)


def test_a_chain_longer_than_a_node_will_walk_is_refused_before_any_of_it_is_checked():
    """The chain arrives from an unauthenticated endpoint, so its length is somebody else's
    choice and the work has to be bounded before it starts."""
    keys = [generate("ed25519") for _ in range(CHAIN_MAX + 3)]
    chain = [
        sign(pem(keys[i]), next_pin=pin_of(keys[i + 1]), compromised=False)
        for i in range(len(keys) - 1)
    ]

    with pytest.raises(HandOverInvalid) as caught:
        follow(chain, pin_of(keys[0]))

    assert str(CHAIN_MAX) in str(caught.value)


# ------------------------------------------------------------ agreement with the pin itself


def test_the_pin_of_a_certificate_and_the_pin_of_its_key_are_the_same_value(tmp_path):
    """The hand-over hashes a bare SPKI; pairing hashes one out of a certificate. If those
    two ever disagreed, every hand-over would be `HandOverNotMine` and the cause would be
    nowhere near the symptom."""
    _, _, pin, key = make_certificate(tmp_path, "core")

    assert pin_of(key) == pin


def test_a_renewed_certificate_on_the_same_key_needs_no_hand_over(tmp_path):
    """Certificate renewal is the common case and must stay a non-event — which is why
    ADR 0041 pinned the SPKI rather than the certificate in the first place. A hand-over is
    for the rarer thing: the key underneath actually changing."""
    _, _, first, key = make_certificate(tmp_path, "core")
    _, _, renewed, _ = make_certificate(tmp_path, "renewed", key=key)
    _, _, replaced, _ = make_certificate(tmp_path, "replaced")

    assert renewed == first
    assert replaced != first


def test_issued_at_orders_a_chain_for_the_owner():
    """Not a security field — the node walks by signature, not by date — but the owner
    reading a file wants to know when their core's key changed."""
    a, b = generate("ed25519"), generate("ed25519")
    when = datetime(2026, 3, 1, 9, 30, tzinfo=UTC)

    handover = sign(pem(a), next_pin=pin_of(b), issued_at=when, compromised=False)

    assert handover.issued_at == when
    assert HandOver.from_dict(handover.to_dict()).issued_at == when
    verify(HandOver.from_dict(handover.to_dict()), pin_of(a))


def test_the_date_is_signed_so_it_cannot_be_quietly_moved():
    a, b = generate("ed25519"), generate("ed25519")
    handover = sign(pem(a), next_pin=pin_of(b), compromised=False)

    moved = replace(handover, issued_at=handover.issued_at - timedelta(days=400))

    with pytest.raises(HandOverInvalid):
        verify(moved, pin_of(a))


# ------------------------------------------------------------------ what the documents claim


def test_the_threat_model_no_longer_says_pinning_is_unimplemented():
    """It said so for sixty-odd sprints after ADR 0041 shipped it.

    Not a flattering error — it understated what was built — but ADR 0065's point was that an
    unchecked claim is wrong in whichever direction nobody is looking, and a threat model that
    only ever grows is one nobody re-reads.
    """
    from pathlib import Path

    text = Path("docs/14-threat-model.md").read_text(encoding="utf-8")

    assert "pinning is not implemented" not in text
    assert "session tokens are not yet" not in text


def test_both_documents_name_what_a_hand_over_cannot_do():
    """The two refusals are the honest half of this feature, so they are guarded like a count.

    A reader who takes "the TLS key rotates" without them would believe Thursday holds that
    key, and that rotating away from a stolen one is a fix rather than a race.
    """
    from pathlib import Path

    # Line breaks in prose move; the sentence is the claim. Normalised rather than matched
    # verbatim so a reflow does not fail a test about meaning.
    def prose(page: str) -> str:
        return " ".join(Path(page).read_text(encoding="utf-8").split())

    for page in ("docs/23-release-readiness.md", "docs/14-threat-model.md"):
        assert "compromised key cannot be handed over" in prose(page), page

    assert "Thursday does not hold the core's TLS key" in prose("docs/23-release-readiness.md")
