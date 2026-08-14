"""Deliberate import-boundary violation for Sprint 0 gate-verification evidence.

domain/ must import nothing from any other internal layer (Blueprint §3.3,
ADR-3.4). This file imports from aether.adapters to prove import-linter's
CI gate rejects it. Never merged (SPRINT_0_PLAN §21 deliverable 5).
"""
from aether import adapters  # noqa: F401 — the violation under test
