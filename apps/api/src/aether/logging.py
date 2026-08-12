"""Structured JSON logging (Blueprint §3.8).

Contract from day one: structured JSON only; a ``correlation_id`` field on
every event (bound by middleware); prompts/completions are NEVER logged by
default. This module owns process-wide logging configuration for both the
API and worker entrypoints.
"""

from __future__ import annotations

import logging
import sys
from typing import Final

import structlog

_LEVELS: Final[dict[str, int]] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}

_SHARED_PROCESSORS: list[structlog.typing.Processor] = [
    structlog.contextvars.merge_contextvars,
    structlog.processors.add_log_level,
    structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
    structlog.processors.StackInfoRenderer(),
    structlog.processors.format_exc_info,
]


def configure_logging(*, level: str, service_name: str) -> None:
    """Configure structlog + stdlib to emit one JSON object per line."""
    logging.basicConfig(stream=sys.stdout, level=level, format="%(message)s")
    structlog.configure(
        processors=[
            *_SHARED_PROCESSORS,
            structlog.processors.EventRenamer("event"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(_LEVELS[level]),
        cache_logger_on_first_use=True,
    )
    structlog.contextvars.bind_contextvars(service=service_name)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a named, bound structlog logger."""
    return structlog.get_logger(name)  # type: ignore[no-any-return]
