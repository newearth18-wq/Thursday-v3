"""Settings (§99).

Everything configurable comes from the environment or a ``.env`` file. Nothing that
differs between machines is hard-coded, and no default reaches for the cloud: a fresh
checkout boots fully local (§58).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from thursday_shared.enums import ProactivityLevel


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="THURSDAY_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # identity ------------------------------------------------------------------
    assistant_name: str = "Thursday"
    owner_name: str = "Owner"
    locale: str = "th-TH"
    timezone: str = "Asia/Bangkok"
    proactivity: ProactivityLevel = ProactivityLevel.NORMAL

    # runtime -------------------------------------------------------------------
    environment: str = "development"
    debug: bool = False
    host: str = "127.0.0.1"
    port: int = 8000
    data_dir: Path = Path("var")
    log_level: str = "INFO"
    log_json: bool = False

    # storage -------------------------------------------------------------------
    database_url: str = "sqlite+aiosqlite:///var/thursday.db"
    redis_url: str | None = None  # None ⇒ in-process event bus and queue

    # models --------------------------------------------------------------------
    #: "rule" (offline, deterministic), "ollama", or "anthropic".
    llm_backend: str = "rule"
    llm_fast_model: str = "claude-haiku-4-5-20251001"
    llm_standard_model: str = "claude-sonnet-5"
    llm_reasoning_model: str = "claude-opus-5"
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.1:8b"
    anthropic_api_key_handle: str = "anthropic_api_key"
    #: Hard ceiling on what may leave the machine, regardless of the router's preference.
    allow_cloud: bool = True

    # voice ---------------------------------------------------------------------
    wake_word: str = "thursday"
    stt_backend: str = "stub"  # stub | whisper
    tts_backend: str = "stub"  # stub | piper
    voice_name: str = "thursday-neutral"

    # memory --------------------------------------------------------------------
    embedding_backend: str = "hash"  # hash (offline) | ollama | cloud
    embedding_dimensions: int = 256
    obsidian_vault: Path = Path("thursday_vault")
    obsidian_enabled: bool = True
    memory_working_ttl_hours: int = 24

    # execution -----------------------------------------------------------------
    max_plan_steps: int = 12
    max_step_attempts: int = 2
    max_dynamic_agents_per_task: int = 4
    max_agent_depth: int = 2
    default_task_budget_usd: float = 0.50
    default_task_budget_seconds: float = 300.0
    approval_ttl_seconds: float = 300.0
    device_action_timeout_s: float = 30.0

    # security ------------------------------------------------------------------
    vault_backend: str = "env"  # env | memory | keychain
    #: A vault *handle*, not a secret. The value never appears in configuration.
    device_shared_secret_handle: str = "device_enrollment_secret"  # noqa: S105
    require_device_signature: bool = True

    @property
    def offline(self) -> bool:
        return self.llm_backend == "rule" or not self.allow_cloud

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if self.obsidian_enabled:
            self.obsidian_vault.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
