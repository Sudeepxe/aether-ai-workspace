"""Application settings — environment-driven, 12-factor (Blueprint §3.9.2).

Sprint 0 scope: only what the bootstrap skeleton needs. Every future
variable must also appear in the repo-root ``.env.example`` (drift guard
in ``make lint``, SPRINT_0_PLAN §15).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-level configuration, injected via environment."""

    model_config = SettingsConfigDict(env_prefix="AETHER_", frozen=True)

    env: Literal["dev", "test", "staging", "prod"] = "dev"
    service_name: str = "aether-api"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton (cached, frozen)."""
    return Settings()
