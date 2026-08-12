"""Worker entrypoint — Sprint 0 skeleton.

Starts, logs, and waits for SIGTERM/SIGINT with a graceful exit so the
container contract (healthcheck + clean shutdown, Blueprint §3.2.8) is
honoured before any consumer exists. Queue consumers land in S5.
"""

from __future__ import annotations

import signal
import threading
from types import FrameType

from aether.config import get_settings
from aether.logging import configure_logging, get_logger

log = get_logger(__name__)


def run() -> None:
    """Run the (currently consumer-less) worker until signalled."""
    settings = get_settings()
    configure_logging(level=settings.log_level, service_name="aether-worker")

    stop = threading.Event()

    def _handle(signum: int, _frame: FrameType | None) -> None:
        log.info("shutdown_signal", signal=signal.Signals(signum).name)
        stop.set()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)

    log.info("worker_started", note="no consumers registered yet (S5)")
    stop.wait()
    log.info("worker_stopped")


if __name__ == "__main__":
    run()
