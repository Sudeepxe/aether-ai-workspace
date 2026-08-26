"""Exports the real running app's OpenAPI spec to packages/contracts/
(S10 #107, ADR-9.2: contracts are generated artifacts only, never
hand-edited).

Deliberately does *not* boot the app's lifespan (no live Postgres/Redis
needed) — the spec is derived entirely from route declarations and
Pydantic models via ``FastAPI.openapi()``, the same computation
``docs_url="/docs"`` triggers in dev; nothing about it depends on the
app actually being able to serve a request.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_OUTPUT_PATH = _REPO_ROOT / "packages" / "contracts" / "openapi.json"


def export() -> dict[str, object]:
    from aether.http.app import create_app

    app = create_app()
    spec: dict[str, object] = app.openapi()
    return spec


def main() -> int:
    check_only = "--check" in sys.argv
    spec = export()
    rendered = json.dumps(spec, indent=2, sort_keys=True) + "\n"

    if check_only:
        current = _OUTPUT_PATH.read_text() if _OUTPUT_PATH.exists() else ""
        if current != rendered:
            print(
                f"openapi.json is out of date — run `make openapi` and commit the result.\n"
                f"({_OUTPUT_PATH} does not match the app's current spec)",
                file=sys.stderr,
            )
            return 1
        print("openapi.json is up to date.")
        return 0

    _OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _OUTPUT_PATH.write_text(rendered)
    print(f"wrote {_OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
