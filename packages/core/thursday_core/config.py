"""Settings (PART 85).

Precedence, lowest to highest:

    defaults in code  <  settings.yaml  <  .env  <  process environment

Nothing here reaches for the cloud by default: a fresh checkout boots fully local on
SQLite and an in-process bus (ADR 0006), and the locked production stack is one
`docker compose up` away.

Secrets are not settings. They live behind the ``SecretProvider`` and are referenced here
only by handle, so no password is ever written into a tracked file.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict
from thursday_shared.enums import AutonomyLevel, ProactivityLevel

from thursday_core.learning import TeachingFrequency
from thursday_core.logging import get_logger

log = get_logger(__name__)

SETTINGS_FILE = Path("settings.yaml")

#: settings.yaml is grouped for humans; Settings is flat for code. This maps one to the
#: other, so a reader can find any field in either place.
_YAML_MAP: dict[str, dict[str, str]] = {
    "identity": {
        "assistant_name": "assistant_name",
        "owner_name": "owner_name",
        "locale": "locale",
        "timezone": "timezone",
        "proactivity": "proactivity",
        "autonomy": "autonomy",
    },
    "database": {
        "driver": "db_driver",
        "host": "db_host",
        "port": "db_port",
        "name": "db_name",
        "user": "db_user",
        "pool_size": "db_pool_size",
        "echo": "debug",
    },
    "edition": {"name": "edition"},
    "teaching": {"frequency": "teaching"},
    "redis": {"url": "redis_url"},
    "models": {
        "backend": "llm_backend",
        "allow_cloud": "allow_cloud",
        "fast": "llm_fast_model",
        "standard": "llm_standard_model",
        "reasoning": "llm_reasoning_model",
        "ollama_url": "ollama_url",
        "ollama_model": "ollama_model",
        "anthropic_key_handle": "anthropic_api_key_handle",
    },
    "voice": {
        "wake_word": "wake_word",
        "stt": "stt_backend",
        "tts": "tts_backend",
        "voice_name": "voice_name",
        "always_ready": "voice_always_ready",
        "barge_in": "voice_barge_in",
    },
    "vision": {
        "camera_enabled": "camera_enabled",
        "gesture_timeout_s": "gesture_timeout_s",
        "observation_retention_days": "observation_retention_days",
    },
    "memory": {
        "embedding_backend": "embedding_backend",
        "embedding_dimensions": "embedding_dimensions",
        "working_ttl_hours": "memory_working_ttl_hours",
        "obsidian_enabled": "obsidian_enabled",
        "obsidian_vault": "obsidian_vault",
    },
    "devices": {
        "heartbeat_s": "device_heartbeat_s",
        "stale_after_s": "device_stale_after_s",
        "require_signature": "require_device_signature",
        "action_timeout_s": "device_action_timeout_s",
        "session_max_hours": "device_session_max_hours",
        "credential_max_age_days": "device_credential_max_age_days",
    },
    "permissions": {"approval_ttl_seconds": "approval_ttl_seconds"},
    "compute": {
        "routing_mode": "ai_routing_mode",
        "routing_profile": "ai_routing_profile",
    },
    "limits": {
        "default_per_minute": "rate_limit_default_per_minute",
        "expensive_per_minute": "rate_limit_expensive_per_minute",
        "approvals_per_minute": "rate_limit_approvals_per_minute",
        "pairing_per_minute": "rate_limit_pairing_per_minute",
        "trusted_proxies": "trusted_proxies",
    },
    "execution": {
        "max_plan_steps": "max_plan_steps",
        "max_step_attempts": "max_step_attempts",
        "max_dynamic_agents_per_task": "max_dynamic_agents_per_task",
        "max_agent_depth": "max_agent_depth",
        "default_task_budget_usd": "default_task_budget_usd",
        "default_task_budget_seconds": "default_task_budget_seconds",
        "daily_cost_cap_usd": "daily_cost_cap_usd",
        "monthly_cost_cap_usd": "monthly_cost_cap_usd",
    },
    "persistence": {
        "memory": "persist_memory",
        "audit": "persist_audit",
        "costs": "persist_costs",
        "tasks": "persist_tasks",
        "models": "persist_models",
        "owner_id": "owner_id",
    },
    "updates": {
        "channel_url": "update_channel_url",
        "signing_key": "update_signing_key",
        "manifest_path": "update_manifest_path",
    },
    "logging": {"level": "log_level", "json": "log_json"},
}


class YamlSettingsSource(PydanticBaseSettingsSource):
    """Reads settings.yaml, flattening the human-facing groups into field names."""

    def __init__(self, settings_cls: type[BaseSettings], path: Path = SETTINGS_FILE) -> None:
        super().__init__(settings_cls)
        self._path = path

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            import yaml

            raw = yaml.safe_load(self._path.read_text(encoding="utf-8")) or {}
        except Exception:
            return {}

        flat: dict[str, Any] = {}
        for group, mapping in _YAML_MAP.items():
            section = raw.get(group) or {}
            if not isinstance(section, dict):
                continue
            for yaml_key, field_name in mapping.items():
                if yaml_key in section:
                    flat[field_name] = section[yaml_key]
        return flat


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="THURSDAY_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # identity ------------------------------------------------------------------
    assistant_name: str = "Thursday"
    owner_name: str = "Owner"
    locale: str = "th-TH"
    timezone: str = "Asia/Bangkok"
    #: When Thursday may *speak* unprompted.
    proactivity: ProactivityLevel = ProactivityLevel.NORMAL
    #: When Thursday may *act* unasked. Separate axis, separate risk (ADR 0009).
    autonomy: AutonomyLevel = AutonomyLevel.MODERATE
    #: How often Thursday teaches unprompted (§7, §39): OFF · ON_REQUEST · LOW · NORMAL · HIGH.
    #: A third axis, and separate from the two above for the same reason they are separate
    #: from each other: how much Thursday *explains* is not how much it acts or announces.
    #: OFF and ON_REQUEST are ceilings — no relevance score reaches past them.
    teaching: str = "NORMAL"

    # runtime -------------------------------------------------------------------
    environment: str = "development"
    debug: bool = False
    host: str = "127.0.0.1"
    port: int = 8000
    data_dir: Path = Path("var")
    log_level: str = "INFO"
    log_json: bool = False

    # edition -------------------------------------------------------------------
    #: Which shape of Thursday this install is (EASY INSTALL §"Deployment editions").
    #:
    #: This is the setting the easy-install requirement turns on. `desktop` must run on a
    #: machine where nothing has been installed and nothing has been configured — no
    #: database server, no Redis, no Docker, no terminal. `hub` is the multi-device
    #: deployment that earns those dependencies by needing them. `developer` is `hub` with
    #: everything visible.
    #:
    #: `external_services()` reports what each one actually demands, and a test asserts
    #: that `desktop` demands nothing — because "it just works" is a claim, and a claim
    #: that nothing checks is the class of bug this project keeps finding.
    edition: str = "desktop"

    # storage -------------------------------------------------------------------
    #: Set this to override the composed URL entirely (a managed database, say).
    database_url: str | None = None
    db_driver: str = "sqlite+aiosqlite"
    db_host: str | None = None
    db_port: int = 5432
    db_name: str = "thursday"
    db_user: str = "thursday"
    #: Read from the environment, never from a tracked file.
    db_password: str | None = None
    db_pool_size: int = 10
    #: None ⇒ in-process event bus and queue (ADR 0006).
    redis_url: str | None = None

    # models --------------------------------------------------------------------
    llm_backend: str = "rule"  # rule | ollama | anthropic
    llm_fast_model: str = "claude-haiku-4-5-20251001"
    llm_standard_model: str = "claude-sonnet-5"
    llm_reasoning_model: str = "claude-opus-5"
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.1:8b"
    anthropic_api_key_handle: str = "anthropic_api_key"
    #: Hard ceiling on what may leave the machine, whatever the router prefers.
    allow_cloud: bool = True

    # voice ---------------------------------------------------------------------
    wake_word: str = "thursday"
    stt_backend: str = "stub"  # stub | whisper
    tts_backend: str = "stub"  # stub | piper
    voice_name: str = "thursday-neutral"
    #: Refuse to send audio to a non-local provider, whatever the chain says (§34).
    voice_local_only: bool = True
    #: §46 — let spoken output follow the owner between devices. Off by default: audio
    #: moving on its own is a surprise, and a surprise in a room with other people in it
    #: is the kind that ends with the feature turned off.
    voice_follow_me: bool = False
    #: Nothing is transcribed until the name is heard (T9). Disabled only for push-to-talk.
    require_wake_word: bool = True
    voice_always_ready: bool = False
    voice_barge_in: bool = True

    # vision --------------------------------------------------------------------
    #: PART 51 — the camera is off until it is asked for.
    camera_enabled: bool = False
    gesture_timeout_s: float = 10.0
    observation_retention_days: int = 7

    # memory --------------------------------------------------------------------
    embedding_backend: str = "hash"  # hash (offline) | ollama
    embedding_dimensions: int = 256
    obsidian_vault: Path = Path("thursday_vault")
    obsidian_enabled: bool = True
    memory_working_ttl_hours: int = 24

    # devices -------------------------------------------------------------------
    device_heartbeat_s: float = 15.0
    device_stale_after_s: float = 90.0
    device_action_timeout_s: float = 30.0
    require_device_signature: bool = True
    #: How long one authenticated node session may last before the node must prove its
    #: identity again (§79). There is deliberately no "unbounded" value: a session that
    #: never expires is one whose authentication can outlive the key that produced it,
    #: which is precisely what rotation is supposed to end. Raise it if reconnects are
    #: expensive on your link; you cannot switch it off.
    device_session_max_hours: float = Field(default=12.0, ge=0.25)
    #: How long a device credential is expected to go between rotations (§117). Reported,
    #: never enforced — see ADR 0042 for why an expiring device key is an outage rather
    #: than a control.
    device_credential_max_age_days: int = Field(default=180, ge=1)

    # AI compute routing (ADDENDUM §15, §46) -------------------------------------
    #: §15's recommended default. LOCAL_FIRST rather than AUTO: the addendum names both as
    #: reasonable, and preferring the owner's own hardware is the choice that needs no
    #: justification when somebody asks where their data went.
    ai_routing_mode: str = "LOCAL_FIRST"
    ai_routing_profile: str = "BALANCED"

    #: Where magic packets go (ADDENDUM §20). The all-networks broadcast by default; a
    #: deployment with several subnets sets the directed broadcast for the one its machines
    #: are on, because 255.255.255.255 does not cross a router and a packet that never
    #: arrives looks exactly like a machine that would not wake.
    wake_broadcast: str = "255.255.255.255"

    # rate limits (§128) --------------------------------------------------------
    #: Requests allowed per minute, per calling address, per class. Generous by design: this
    #: exists to stop a runaway loop, not to ration the owner. A limit tight enough to
    #: interrupt legitimate work is one somebody switches off, and then there is none.
    rate_limit_default_per_minute: int = Field(default=240, ge=1)
    #: Anything that can reach a model. The spend ledger caps the money after the fact; this
    #: caps the rate before the call.
    rate_limit_expensive_per_minute: int = Field(default=30, ge=1)
    #: Approvals (§128). Low because a human is on the other end of every one of these, and
    #: because a flood of approval traffic is either a bug or an attempt at approval fatigue.
    rate_limit_approvals_per_minute: int = Field(default=60, ge=1)
    #: The HTTP layer in front of `PairingService`'s own guess budget, which stays the real
    #: control — this only stops the requests arriving that fast in the first place.
    rate_limit_pairing_per_minute: int = Field(default=20, ge=1)
    #: Addresses whose `X-Forwarded-For` may be believed. Empty by default: until a
    #: deployment names its reverse proxy, every request behind one shares a single bucket,
    #: which is a visible degradation rather than a header anybody can forge.
    trusted_proxies: tuple[str, ...] = ()

    # execution -----------------------------------------------------------------
    max_plan_steps: int = 12
    max_step_attempts: int = 2
    max_dynamic_agents_per_task: int = 4
    max_agent_depth: int = 2
    default_task_budget_usd: float = 0.50
    default_task_budget_seconds: float = 300.0
    #: Ceilings above any single task (§61, Sprint 45). A per-task budget stops one runaway
    #: task and does nothing about five hundred small ones. None means no ceiling — stated
    #: rather than defaulted to a number that would look like a decision somebody made.
    daily_cost_cap_usd: float | None = 5.0
    monthly_cost_cap_usd: float | None = 50.0
    approval_ttl_seconds: float = 300.0

    # persistence ---------------------------------------------------------------
    #: Whether memories outlive the process (Sprint 51). Off by default: the schema, the
    #: migrations and the offline path all have to work without it, and a default that
    #: silently created a database file would make "no infrastructure" untrue.
    persist_memory: bool = False
    persist_audit: bool = False
    persist_costs: bool = False
    persist_tasks: bool = False
    persist_models: bool = False
    #: Thursday is single-tenant. This is the owner row the seeds create.
    owner_id: UUID = UUID("00000000-0000-0000-0000-000000000001")

    # updates -------------------------------------------------------------------
    #: Where this deployment looks for updates, and the key it trusts to sign them (§120).
    #: Configuration, deliberately: nothing at runtime can change either, so there is no
    #: parameter a model could put a download location into.
    update_channel_url: str = ""
    update_signing_key: str = ""
    update_manifest_path: Path | None = None

    # security ------------------------------------------------------------------
    vault_backend: str = "env"  # env | memory | keychain
    device_shared_secret_handle: str = "device_enrollment_secret"  # noqa: S105

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Highest precedence first.
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlSettingsSource(settings_cls),
            file_secret_settings,
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def resolved_database_url(self) -> str:
        """The URL actually used. Composed from parts unless one was given outright."""
        if self.database_url:
            return self.database_url
        if self.db_driver.startswith("sqlite"):
            return f"{self.db_driver}:///{self.data_dir}/thursday.db"
        credentials = self.db_user
        if self.db_password:
            credentials = f"{self.db_user}:{self.db_password}"
        host = self.db_host or "localhost"
        return f"{self.db_driver}://{credentials}@{host}:{self.db_port}/{self.db_name}"

    @property
    def offline(self) -> bool:
        return self.llm_backend == "rule" or not self.allow_cloud

    @property
    def uses_postgres(self) -> bool:
        return "postgres" in self.resolved_database_url

    @property
    def is_desktop(self) -> bool:
        return self.edition.lower() == "desktop"

    @property
    def teaching_frequency(self) -> TeachingFrequency:
        """`teaching` as the enum the tutor compares against (§7, §39).

        An unrecognised value falls back to OFF rather than to the default. A typo in this
        setting is somebody trying to turn teaching *down*, and the safe reading of a
        misspelled instruction is the quiet one — the loud failure mode here is Thursday
        talking over a person who asked it not to.
        """
        try:
            return TeachingFrequency[self.teaching.strip().upper()]
        except KeyError:
            log.warning("unknown_teaching_frequency", value=self.teaching)
            return TeachingFrequency.OFF

    def external_services(self) -> list[str]:
        """What somebody has to install and run before this configuration works.

        The whole point of the desktop edition is that this list is empty. Returning it —
        rather than asserting it somewhere — means the installer, the health check and the
        test suite all ask the same question of the same code, and a future setting that
        quietly adds a dependency shows up in all three at once.
        """
        needed = []
        if self.uses_postgres:
            needed.append("PostgreSQL")
        if self.redis_url:
            needed.append("Redis")
        return needed

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if self.obsidian_enabled:
            self.obsidian_vault.mkdir(parents=True, exist_ok=True)

    def redacted(self) -> dict[str, Any]:
        """A view safe to log or return from /health: no password, composed or otherwise."""
        data = self.model_dump(mode="json", exclude={"db_password", "database_url"})
        data["resolved_database_url"] = _mask_credentials(self.resolved_database_url)
        return data


def _mask_credentials(url: str) -> str:
    if "://" not in url or "@" not in url:
        return url
    scheme, _, rest = url.partition("://")
    _, _, host = rest.rpartition("@")
    return f"{scheme}://***@{host}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Tests and the CLI construct their own Settings; this clears the process-wide one."""
    get_settings.cache_clear()
