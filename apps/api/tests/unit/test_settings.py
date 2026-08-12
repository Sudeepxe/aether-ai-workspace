"""Settings contract tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aether.config import Settings

pytestmark = pytest.mark.unit


def test_defaults_are_dev_safe() -> None:
    s = Settings()
    assert s.env == "dev"
    assert s.log_level == "INFO"


def test_settings_are_frozen() -> None:
    s = Settings()
    with pytest.raises(ValidationError):
        s.env = "prod"  # type: ignore[misc]


def test_env_prefix_is_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AETHER_LOG_LEVEL", "DEBUG")
    assert Settings().log_level == "DEBUG"
