"""Observability (S9, NFR-O-1, §3.8): tracing + metrics.

Split from ``logging.py`` (which already handles structured JSON logs
and the correlation-id contextvar) — this package owns OpenTelemetry
tracing and Prometheus metrics, the two other legs of the LGTM stack.
"""
