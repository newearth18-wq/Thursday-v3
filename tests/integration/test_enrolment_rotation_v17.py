"""The enrolment rotation, configured the way an owner would configure it (§117, V17)."""

from __future__ import annotations

import pytest
from thursday_core.config import Settings
from thursday_core.container import _build_device_auth, _build_rotation
from thursday_security.enrolment import fingerprint
from thursday_shared.errors import ConfigurationError

CURRENT = "THURSDAY_SECRET_DEVICE_ENROLLMENT_SECRET"
PREVIOUS = "THURSDAY_SECRET_DEVICE_ENROLLMENT_SECRET_PREVIOUS"


@pytest.fixture
def env(monkeypatch):
    for key in (CURRENT, PREVIOUS):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def settings(**overrides) -> Settings:
    return Settings(require_device_signature=True, **overrides)


def test_no_rotation_configured_is_the_ordinary_case(env):
    env.setenv(CURRENT, "only-token")
    auth = _build_device_auth(settings())
    assert auth.configured
    assert auth.rotation is not None
    assert auth.rotation.retiring == ""
    assert auth.rotation.to_dict()["rotating"] is False
    assert auth.rotation.accepts() == ("only-token",)


def test_a_complete_rotation_is_read_from_the_environment(env):
    env.setenv(CURRENT, "the-new-one")
    env.setenv(PREVIOUS, "the-old-one")
    auth = _build_device_auth(settings(device_enrolment_retires_at="2026-12-31T00:00:00+07:00"))

    assert auth.rotation is not None
    shown = auth.rotation.to_dict()
    assert shown["rotating"] is True
    assert shown["current"] == fingerprint("the-new-one")
    assert shown["retiring"] == fingerprint("the-old-one")


def test_what_is_shown_never_contains_a_secret(env):
    env.setenv(CURRENT, "the-new-one")
    env.setenv(PREVIOUS, "the-old-one")
    auth = _build_device_auth(settings(device_enrolment_retires_at="2026-12-31T00:00:00+07:00"))
    assert "the-new-one" not in str(auth.rotation.to_dict())
    assert "the-old-one" not in str(auth.rotation.to_dict())


# ------------------------------------------------- half a rotation fails where somebody looks


def test_a_previous_token_with_no_retirement_date_is_refused_at_startup(env):
    """The dangerous shape: a second live secret with no end, while the owner believes the
    rotation finished. It fails at startup rather than at the first HELLO months later."""
    env.setenv(CURRENT, "the-new-one")
    env.setenv(PREVIOUS, "the-old-one")
    with pytest.raises(ConfigurationError, match="retirement date"):
        _build_rotation(settings(), "the-new-one")


def test_a_retirement_date_with_no_previous_token_is_refused(env):
    env.setenv(CURRENT, "the-new-one")
    with pytest.raises(ConfigurationError, match="previous token"):
        _build_rotation(settings(device_enrolment_retires_at="2026-12-31T00:00:00+07:00"), "x")


def test_a_date_with_no_timezone_is_refused_rather_than_guessed(env):
    """ "Which timezone did they mean" is not a question to guess at for the moment a secret
    stops working."""
    env.setenv(CURRENT, "the-new-one")
    env.setenv(PREVIOUS, "the-old-one")
    with pytest.raises(ConfigurationError, match="timezone"):
        _build_rotation(settings(device_enrolment_retires_at="2026-12-31T00:00:00"), "the-new-one")


def test_an_unparseable_date_names_what_it_could_not_read(env):
    env.setenv(CURRENT, "the-new-one")
    env.setenv(PREVIOUS, "the-old-one")
    with pytest.raises(ConfigurationError, match="ISO-8601"):
        _build_rotation(settings(device_enrolment_retires_at="สิ้นปี"), "the-new-one")


def test_rotating_to_the_same_value_is_refused(env):
    """What this looks like in practice is setting the new variable to the old value."""
    env.setenv(CURRENT, "same")
    env.setenv(PREVIOUS, "same")
    with pytest.raises(ConfigurationError, match="เหมือนกัน"):
        _build_rotation(settings(device_enrolment_retires_at="2026-12-31T00:00:00+07:00"), "same")


def test_a_previous_token_without_a_current_one_is_refused(env):
    env.setenv(PREVIOUS, "the-old-one")
    with pytest.raises(ConfigurationError, match="current one is not"):
        _build_rotation(settings(device_enrolment_retires_at="2026-12-31T00:00:00+07:00"), None)
