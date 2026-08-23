"""Golden-case schema (§6.4's four-class dataset) — plain dataclasses
loaded from JSON, no framework: the same "plain owned code" posture
ADR-6.1 already established for the retrieval pipeline itself.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class CaseClass(StrEnum):
    ANSWERABLE = "answerable"
    UNANSWERABLE = "unanswerable"
    ADVERSARIAL = "adversarial"
    MULTI_TURN = "multi_turn"


@dataclass(frozen=True, slots=True)
class GoldenTurn:
    query: str
    expect_grounded: bool
    """The real, mechanically-checkable Gate 1 outcome this turn must
    produce (issue #60) — True for answerable/adversarial/multi-turn
    turns whose answer is genuinely in the corpus, False for an
    unanswerable turn."""
    expect_gold_document: str | None = None
    """The corpus filename (matches ``documents.filename``) a correct
    answer's citation(s) should point at. None for a turn with no
    single expected source (e.g. an unanswerable turn)."""
    expect_gold_section_contains: str | None = None
    """A case-insensitive substring the correct citation's
    ``section_path`` should contain — coarser than an exact chunk id
    (which isn't stable across ingestion runs), still a real check."""
    adversarial_trigger_phrase: str | None = None
    """Only set on an adversarial turn's injection payload — if this
    exact phrase appears verbatim in the *reply*, the injection may have
    been followed rather than treated as inert data (§2's untrusted-
    content principle). See metrics.py's docstring for why this is a
    real but limited proxy against EchoGenerator specifically."""


@dataclass(frozen=True, slots=True)
class GoldenCase:
    id: str
    case_class: CaseClass
    corpus_files: list[str]
    """Filenames under evals/corpora/ — ingested fresh into an isolated
    workspace before this case's turns run."""
    turns: list[GoldenTurn]
    rubric: str | None = None
    """Written faithfulness rubric for the LLM judge (issue #71) — None
    for classes the judge doesn't score (e.g. adversarial's target is
    mechanical safety, not faithfulness)."""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GoldenCase:
        return cls(
            id=data["id"],
            case_class=CaseClass(data["case_class"]),
            corpus_files=list(data["corpus_files"]),
            turns=[
                GoldenTurn(
                    query=t["query"],
                    expect_grounded=t["expect_grounded"],
                    expect_gold_document=t.get("expect_gold_document"),
                    expect_gold_section_contains=t.get("expect_gold_section_contains"),
                    adversarial_trigger_phrase=t.get("adversarial_trigger_phrase"),
                )
                for t in data["turns"]
            ],
            rubric=data.get("rubric"),
        )


def load_golden_set(golden_dir: Path) -> list[GoldenCase]:
    """Loads every ``*.json`` case file directly under ``golden_dir``
    (not recursive — each version gets its own directory, e.g.
    ``evals/golden/v1/``), sorted by id for a deterministic run order."""
    cases = [
        GoldenCase.from_dict(json.loads(path.read_text()))
        for path in sorted(golden_dir.glob("*.json"))
    ]
    return sorted(cases, key=lambda c: c.id)
