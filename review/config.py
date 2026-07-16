"""Environment-driven settings for the AI review pipeline."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All review configuration is env-driven; see README for variable docs."""

    model_config = SettingsConfigDict(
        env_file=(".env", "scripts/.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    ollama_base_url: str = Field(
        default="http://10.28.81.52:11434",
        alias="OLLAMA_BASE_URL",
    )
    ollama_model: str = Field(default="granite3.2:latest", alias="OLLAMA_MODEL")
    max_diff_lines: int = Field(default=2000, alias="MAX_DIFF_LINES")
    llm_timeout_seconds: float = Field(default=90.0, alias="LLM_TIMEOUT_SECONDS")
    llm_max_retries: int = Field(default=2, alias="LLM_MAX_RETRIES")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # Comma-separated extra ignore globs (appended to defaults in diff_parser)
    review_ignore_extra: str = Field(default="", alias="REVIEW_IGNORE_EXTRA")


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()
