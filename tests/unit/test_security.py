"""Redaction, the vault, privacy classification and the audit chain (§34, §35, §39)."""

from __future__ import annotations

import pytest
from thursday_security.audit import AuditEntry, AuditLog
from thursday_security.privacy import PrivacyClassifier, PrivacyZone, PrivacyZoneRegistry
from thursday_security.redaction import REDACTED, SecretRedactor, redact_dict
from thursday_security.vault import ChainVault, InMemoryVault
from thursday_shared.enums import DataSensitivity
from thursday_shared.errors import ConfigurationError, SecretLeakBlocked


@pytest.fixture
def redactor() -> SecretRedactor:
    return SecretRedactor()


@pytest.mark.parametrize(
    "text",
    [
        "sk-ant-api03-abcdefghijklmnopqrstuvwxyz012345",
        "ghp_abcdefghijklmnopqrstuvwxyz0123",
        "AKIAIOSFODNN7EXAMPLE",
        "AIzaSyA1234567890123456789012345678901234",
        "xoxb-1234567890-abcdefghijkl",
        "password: hunter2000",
        "Authorization: Bearer abcdefghijklmnopqrstuvwx",
        "postgres://user:secretpw@db.internal:5432/app",
    ],
)
def test_credential_shapes_are_caught(redactor, text):
    assert redactor.scan(text), f"missed: {text}"
    assert REDACTED in redactor.redact(text).text


def test_ordinary_text_survives_untouched(redactor):
    text = "เปิดไฟล์ grades.xlsx แล้ววิเคราะห์คะแนนนักเรียน 42 คน"
    result = redactor.redact(text)
    assert result.clean and result.text == text


def test_the_key_name_survives_so_the_shape_stays_debuggable(redactor):
    out = redactor.redact("api_key=abcdef123456").text
    assert "api_key" in out and "abcdef123456" not in out


def test_assert_clean_refuses_rather_than_redacting(redactor):
    with pytest.raises(SecretLeakBlocked) as exc:
        redactor.assert_clean("token: abcdef1234567890", where="the vault")
    assert "the vault" in exc.value.message


def test_dict_redaction_covers_nested_values_and_sensitive_keys():
    out = redact_dict(
        {"args": {"password": "p", "path": "/x"}, "notes": ["AKIAIOSFODNN7EXAMPLE", "fine"]}
    )
    assert out["args"]["password"] == REDACTED
    assert out["args"]["path"] == "/x"
    assert out["notes"][0] == REDACTED and out["notes"][1] == "fine"


async def test_the_vault_never_hands_back_a_raw_value():
    vault = InMemoryVault({"anthropic_api_key": "sk-ant-secret"})
    seen: list[str] = []

    async def use(value: str) -> str:
        seen.append(value)
        return "used"

    assert await vault.use("anthropic_api_key", use) == "used"
    assert seen == ["sk-ant-secret"]
    # The only record kept is *that* it was used, never the value.
    assert vault.access_log == ["anthropic_api_key"]
    assert not hasattr(vault, "get")


async def test_an_unknown_handle_raises_rather_than_returning_empty():
    with pytest.raises(ConfigurationError):
        await InMemoryVault().use("missing", lambda v: v)  # type: ignore[arg-type]


async def test_a_chain_vault_prefers_the_first_backend_that_has_the_handle():
    primary = InMemoryVault({"k": "from-primary"})
    secondary = InMemoryVault({"k": "from-secondary", "other": "x"})
    chain = ChainVault(primary, secondary)
    assert await chain.use("k", _identity) == "from-primary"
    assert await chain.use("other", _identity) == "x"


async def _identity(value: str) -> str:
    return value


def test_credential_material_classifies_as_secret():
    classification = PrivacyClassifier().classify("my key is sk-ant-api03-abcdefghijklmnopqrst")
    assert classification.level is DataSensitivity.SECRET
    assert not classification.cloud_allowed


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ผลตรวจสุขภาพและโรคประจำตัว", DataSensitivity.HIGHLY_PRIVATE),
        ("this is confidential and internal only", DataSensitivity.PRIVATE),
        ("what is the weather today", DataSensitivity.PUBLIC),
        ("open the report", DataSensitivity.INTERNAL),
    ],
)
def test_lexical_classification(text, expected):
    assert PrivacyClassifier().classify(text).level is expected


def test_structural_signals_outrank_lexical_ones():
    classification = PrivacyClassifier().classify(
        "what is the weather today", hints={"has_camera_frame": True}
    )
    assert classification.level is DataSensitivity.HIGHLY_PRIVATE
    assert classification.prefers_local


def test_a_privacy_zone_matches_only_where_it_applies():
    zone = PrivacyZone(name="home", location_contexts={"home"}, camera_disabled=True)
    registry = PrivacyZoneRegistry([zone])
    assert registry.forbids("camera", location="home") == "home"
    assert registry.forbids("camera", location="office") is None
    assert registry.forbids("microphone", location="home") is None


def test_the_audit_chain_detects_tampering_and_deletion():
    log = AuditLog()
    for action in ("open_app", "write_file", "send_email"):
        log.record(AuditEntry(action=action, tool=action))
    assert log.verify_chain()

    log._entries[1].action = "something else"
    assert not log.verify_chain()

    log._entries[1].action = "write_file"
    assert log.verify_chain()
    del log._entries[1]
    assert not log.verify_chain()


def test_audit_payloads_are_redacted_at_write_time():
    log = AuditLog()
    entry = log.record(
        AuditEntry(
            action="http_post",
            input_summary={"headers": {"authorization": "Bearer abcdefghijklmnopqrst"}},
            error="failed with key sk-ant-api03-abcdefghijklmnopqrstuv",
        )
    )
    assert entry.input_summary["headers"]["authorization"] == REDACTED
    assert "sk-ant" not in (entry.error or "")
