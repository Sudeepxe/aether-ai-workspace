"""Gate 1 of ADR-6.4's two-gate refusal: the top fused retrieval score
must clear a threshold before the generator is even invoked. Below
threshold is a designed, cheap outcome (§3.2.5: "an empty retrieval is
a feature, not an error") — no generator call, no hallucination risk.

Pure logic, no I/O — takes issue #56's RetrievalResult, decides.
"""

from __future__ import annotations

from dataclasses import dataclass

from aether.app.retrieval.hybrid_search import RetrievalResult


@dataclass(frozen=True, slots=True)
class GateDecision:
    passed: bool
    top_score: float | None
    """None when retrieval returned nothing at all — that's also a
    failure, definitionally: there's no "top score" to have cleared
    any threshold."""


class RetrievalGate:
    def __init__(self, *, threshold: float) -> None:
        self._threshold = threshold

    def evaluate(self, result: RetrievalResult) -> GateDecision:
        if not result.chunks:
            return GateDecision(passed=False, top_score=None)
        top_score = result.chunks[0].fused_score
        return GateDecision(passed=top_score >= self._threshold, top_score=top_score)
