"""Architecture tests — the hexagon's import rules (Blueprint §3.3).

Runs import-linter's contracts as a pytest so boundary violations fail the
ordinary test run, not only the lint lane. This is the Sprint 0 'one real
test per harness' for the architecture layer (SPRINT_0_PLAN §18).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_import_contracts_hold() -> None:
    result = subprocess.run(  # fixed argv, no user input
        [sys.executable, "-m", "importlinter.cli", "lint", "--config", "pyproject.toml"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"Import contracts violated:\n{result.stdout}\n{result.stderr}"
