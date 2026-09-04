"""Staying authenticated, and stopping (BIOMETRIC IDENTITY §87) — Sprint 77.

§80 is the scenario that decides whether the previous four sprints were worth anything, and
it is the ordinary one rather than the exotic one: the owner authenticates, works, walks away,
and somebody else sits down. No attack was mounted and nothing was spoofed. If the session is
still open, every defence built so far was decoration.

The tests below are mostly about the failure direction. A camera that stops reporting, a stale
frame, a hung process — each looks exactly like "the owner is still there" to code that reads
silence as continuity.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from thursday_security.identity import AuthenticationSession, AuthLevel, Factor, UserKind
from thursday_security.presence import (
    AWAY_BEFORE_LOCK,
    DEGRADED_TO,
    PRESENCE_TTL,
    Observation,
    Presence,
    PresenceMonitor,
    SessionGuard,
    privacy_for,
    world_fields,
)

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)


@pytest.fixture
def monitor() -> PresenceMonitor:
    return PresenceMonitor()


@pytest.fixture
def guard(monitor) -> SessionGuard:
    return SessionGuard(monitor=monitor)


def _session(level=AuthLevel.STRONG, **kw) -> AuthenticationSession:
    return AuthenticationSession(
        user_id="owner",
        kind=UserKind.OWNER,
        auth_level=level,
        factors={Factor.FACE, Factor.VOICE},
        started_at=NOW,
        last_verified_at=NOW,
        last_activity_at=NOW,
        **kw,
    )


def _seen(*, at=NOW, who=("owner",), unknown=0) -> Observation:
    return Observation(at=at, recognised=frozenset(who), unknown_people=unknown)


# ================================================= presence must not fail open


def test_silence_is_absence_rather_than_continuity(monitor):
    """The whole defence against a stuck camera. An observation from four minutes ago saying
    the owner was there is not evidence that the owner is there."""
    monitor.observe(_seen(at=NOW))
    assert monitor.state(user_id="owner", now=NOW) is Presence.PRESENT

    stale = NOW + PRESENCE_TTL + timedelta(seconds=1)
    assert monitor.state(user_id="owner", now=stale) is Presence.ABSENT


def test_staleness_is_checked_before_content(monitor):
    """Ordering matters: a stale observation naming the owner must not short-circuit to
    PRESENT on the strength of the name being in it."""
    monitor.observe(_seen(at=NOW, who=("owner",)))
    long_after = NOW + PRESENCE_TTL * 10
    assert monitor.state(user_id="owner", now=long_after) is Presence.ABSENT


def test_never_having_been_seen_is_maximally_away(monitor):
    assert monitor.away_for(user_id="stranger", now=NOW) >= AWAY_BEFORE_LOCK


def test_no_observation_at_all_is_absent(monitor):
    assert monitor.state(user_id="owner", now=NOW) is Presence.ABSENT


# ======================================================== §80 the owner walks away


def test_stepping_out_of_frame_degrades_rather_than_locking(guard, monitor):
    """§24 is a real requirement, not a nicety: a system that re-challenges constantly is one
    people switch off, and a switched-off system protects nobody. Reaching for a coffee cup
    must not mean authenticating again."""
    session = _session()
    monitor.observe(_seen(at=NOW))
    guard.apply(session, now=NOW)

    briefly = NOW + PRESENCE_TTL + timedelta(seconds=5)
    level, note = guard.apply(session, now=briefly)

    assert session.ended_reason == "", "a moment away is not the end of the session"
    # A ladder, not a cliff — and the first version of this assertion was `< STRONG`, which
    # passed at zero while the module's own docstring claimed a gradual degrade. §28 says the
    # level *decreases*; collapsing it makes "degrade" and "lock" the same event.
    assert AuthLevel.NONE < level < AuthLevel.STRONG
    assert level == DEGRADED_TO
    assert "away" in note


def test_a_step_away_keeps_ordinary_work_and_stops_private_work(guard, monitor):
    """The point of the ceiling. §24 wants the owner not re-challenged for reaching for a
    coffee cup; §28 wants private things gone before the lock arrives. Both, at once."""
    from thursday_security.gate import IdentityGate
    from thursday_shared.enums import RiskLevel

    session = _session()
    monitor.observe(_seen(at=NOW))
    guard.apply(session, now=NOW)

    briefly = NOW + PRESENCE_TTL + timedelta(seconds=5)
    guard.apply(session, now=briefly)
    gate = IdentityGate()

    assert (
        gate.check(action="app.open", risk=RiskLevel.LOW, session=session, now=briefly).sufficient
        is True
    )
    assert (
        gate.check(
            action="email.send", risk=RiskLevel.HIGH, session=session, now=briefly
        ).sufficient
        is False
    )


def test_being_seen_again_lifts_the_ceiling(guard, monitor):
    """The ceiling is repaired by the owner glancing at the camera, which is the difference
    between a degraded session and a locked one."""
    session = _session()
    monitor.observe(_seen(at=NOW))
    guard.apply(session, now=NOW)

    briefly = NOW + PRESENCE_TTL + timedelta(seconds=5)
    guard.apply(session, now=briefly)
    assert session.presence_cap is DEGRADED_TO

    back = briefly + timedelta(seconds=10)
    monitor.observe(_seen(at=back))
    level, _ = guard.apply(session, now=back)
    assert session.presence_cap is None
    assert level is AuthLevel.STRONG


def test_being_away_long_enough_locks_the_session(guard, monitor):
    session = _session()
    monitor.observe(_seen(at=NOW))
    guard.apply(session, now=NOW)

    gone = NOW + AWAY_BEFORE_LOCK + timedelta(seconds=1)
    level, note = guard.apply(session, now=gone)

    assert level is AuthLevel.NONE
    assert session.ended_reason
    assert "locked" in note or "away" in note


def test_a_degraded_session_is_no_longer_good_enough_for_private_work(guard, monitor):
    """The point of degrading rather than ending: the session survives and stops being worth
    what it was. Anything needing more than one factor now fails the gate."""
    from thursday_security.gate import IdentityGate
    from thursday_shared.enums import RiskLevel

    session = _session()
    monitor.observe(_seen(at=NOW))
    guard.apply(session, now=NOW)

    briefly = NOW + PRESENCE_TTL + timedelta(seconds=5)
    guard.apply(session, now=briefly)

    verdict = IdentityGate().check(
        action="email.send", risk=RiskLevel.HIGH, session=session, now=briefly
    )
    assert verdict.sufficient is False


# =========================================== §80's second half: somebody else sits down


def test_an_unknown_person_ends_the_session_outright(guard, monitor):
    """Not a degrade. Somebody else is sitting there, and a degraded session is still an
    inheritable one — which is exactly what §80 is about."""
    session = _session()
    monitor.observe(_seen(at=NOW))
    guard.apply(session, now=NOW)

    monitor.observe(_seen(at=NOW + timedelta(seconds=10), who=(), unknown=1))
    level, note = guard.apply(session, now=NOW + timedelta(seconds=11))

    assert level is AuthLevel.NONE
    assert session.ended_reason
    assert "else" in note


def test_an_unknown_person_is_worse_than_an_empty_chair(monitor):
    """Two different states because they have different consequences: an empty chair is not
    a risk, and a stranger at the keyboard is."""
    monitor.observe(_seen(at=NOW, who=(), unknown=1))
    assert monitor.state(user_id="owner", now=NOW) is Presence.UNKNOWN_PERSON

    monitor.observe(_seen(at=NOW, who=()))
    assert monitor.state(user_id="owner", now=NOW) is Presence.ABSENT


def test_an_unknown_person_beside_the_owner_does_not_end_the_session(guard, monitor):
    """The owner is still there. This is a privacy situation, not an authentication one —
    and conflating them would lock the owner out because a colleague walked past."""
    session = _session()
    monitor.observe(_seen(at=NOW, who=("owner",), unknown=1))
    level, _ = guard.apply(session, now=NOW)

    assert session.ended_reason == ""
    assert level > AuthLevel.NONE


# ================================================================== §81 coming back


def test_the_owner_returning_resumes_the_same_session(guard, monitor):
    """§81. The same session id, so running work continues and the audit trail stays one
    thread."""
    session = _session()
    original = session.session_id

    monitor.observe(_seen(at=NOW))
    guard.apply(session, now=NOW)
    guard.apply(session, now=NOW + AWAY_BEFORE_LOCK + timedelta(seconds=1))
    assert session.ended_reason

    back = NOW + AWAY_BEFORE_LOCK + timedelta(minutes=1)
    session.ended_reason = ""  # the identity layer re-establishes them
    level = guard.restore(session, level=AuthLevel.TWO_BIOMETRIC, now=back)

    assert session.session_id == original
    assert level is AuthLevel.TWO_BIOMETRIC


def test_restoring_needs_a_level_from_a_fresh_check(guard):
    """ "They came back" is a claim about a person, and the only thing that establishes a
    person is establishing them. `restore` takes a level rather than computing one, so it
    cannot resurrect a session on the strength of a camera blob."""
    import inspect

    parameters = set(inspect.signature(SessionGuard.restore).parameters)
    assert "level" in parameters
    for shortcut in ("recognised", "trust", "assume", "skip_check"):
        assert shortcut not in parameters


def test_a_session_past_its_outer_bound_cannot_be_restored(guard):
    """Eight hours is eight hours whatever the camera says — a presence signal that never
    fails is indistinguishable from one that is broken."""
    from thursday_security.identity import MAX_SESSION

    session = _session()
    much_later = NOW + MAX_SESSION + timedelta(minutes=1)
    assert guard.restore(session, level=AuthLevel.STRONG, now=much_later) is AuthLevel.NONE


# ================================================================== §29 privacy mode


def test_somebody_else_in_the_room_stops_private_things(monitor):
    """§29, and it is independent of authentication: the owner is perfectly identified and
    there is still somebody else who should not hear their email."""
    monitor.observe(_seen(at=NOW, who=("owner",), unknown=1))
    state = privacy_for(monitor, user_id="owner", now=NOW)
    assert state.private_ok is False
    assert state.others == 1
    assert state.reason


def test_another_authorised_person_also_triggers_privacy(monitor):
    """An authorised colleague is still somebody who should not hear the owner's messages
    read aloud. Counting only *unknown* people would miss the commonest case."""
    monitor.observe(_seen(at=NOW, who=("owner", "colleague")))
    assert privacy_for(monitor, user_id="owner", now=NOW).private_ok is False


def test_alone_means_private_things_are_fine(monitor):
    monitor.observe(_seen(at=NOW, who=("owner",)))
    assert privacy_for(monitor, user_id="owner", now=NOW).private_ok is True


def test_privacy_is_not_derived_from_the_auth_level(monitor):
    """Two switches, two sets of inputs. A single one would either lock the owner out when a
    colleague walks past or read their email aloud because they are authenticated."""
    import inspect

    parameters = set(inspect.signature(privacy_for).parameters)
    for authentication in ("session", "auth_level", "level", "authenticated"):
        assert authentication not in parameters


# =========================================================== §9/§27 nothing streams


def test_an_observation_has_nowhere_to_put_an_image():
    """§27 wants continuous authentication without sending video anywhere. The type is how
    that stays true: nothing downstream can receive a frame it cannot be handed."""
    fields = set(Observation.__annotations__)
    for media in ("frame", "frames", "image", "video", "audio", "embedding", "template"):
        assert media not in fields


def test_the_world_state_fields_carry_no_measurement(monitor):
    """§68's fields reach the planner and therefore a model prompt (§9). Counts and ids
    only — no confidence, no template, no frame."""
    monitor.observe(_seen(at=NOW, who=("owner",), unknown=1))
    fields = world_fields(monitor, _session(), now=NOW)

    assert fields["authenticated_user"] == "owner"
    assert fields["unknown_people_present"] == 1
    assert fields["privacy_mode"] is True
    for measurement in ("confidence", "liveness", "template", "score", "frame"):
        assert measurement not in fields


def test_the_world_state_names_nobody_when_nobody_is_established(monitor):
    """A user id left over from a dead session is an agent acting as somebody."""
    session = _session()
    session.present = False
    fields = world_fields(monitor, session, now=NOW)
    assert fields["authenticated_user"] is None
    assert fields["authentication_level"] == 0


def test_the_world_state_survives_having_seen_nothing(monitor):
    fields = world_fields(monitor, None, now=NOW)
    assert fields["authenticated_user"] is None
    assert fields["authorized_users_present"] == []
    assert fields["last_identity_check"] is None
