"""API entrypoint: ``python -m aether.http.main`` or the api container CMD."""

from __future__ import annotations

import uvicorn

from aether.http.app import create_app


def run() -> None:
    """Run uvicorn. Graceful-shutdown wiring (SSE drain) lands in S3."""
    uvicorn.run(create_app(), host="0.0.0.0", port=8000)  # noqa: S104 — containerized bind-all is intentional


if __name__ == "__main__":
    run()
