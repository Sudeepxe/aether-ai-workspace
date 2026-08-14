"""Architecture tests — the hexagon's import rules (Blueprint §3.3).

Runs import-linter's contracts as a pytest so boundary violations fail the
ordinary test run, not only the lint lane. This is the Sprint 0 'one real
test per harness' for the architecture layer (SPRINT_0_PLAN §18).

Calls importlinter.cli.lint_imports() in-process rather than shelling out
to `python -m importlinter.cli` — the package ships no `__main__.py`, so
that invocation silently does nothing and always exits 0 (discovered
during Sprint 1: this test had never actually caught a violation since
it was written in Sprint 0). lint_imports() here is the same function
the `lint-imports` console script calls internally, just without the
Click/sys.exit wrapper around it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from importlinter.cli import lint_imports

pytestmark = pytest.mark.architecture

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_import_contracts_hold() -> None:
    exit_code = lint_imports(config_filename=str(PROJECT_ROOT / "pyproject.toml"))
    assert exit_code == 0, (
        "import-linter contracts violated — run `uv run lint-imports` for details"
    )
