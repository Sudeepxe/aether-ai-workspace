"""Deliberate lint violation for Sprint 0 gate-verification evidence.

This file exists only to prove the ruff CI gate rejects bad input; it is
never merged (SPRINT_0_PLAN §21 deliverable 5). Violations: unused import,
bare except, mutable default argument.
"""
import os
import sys


def bad_function(items=[]):
    try:
        items.append(1)
    except:
        pass
    return items
