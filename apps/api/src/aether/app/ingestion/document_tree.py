"""The normalized document tree (§3.2.7): every per-format parser
produces this same shape, so structure-aware chunking (ADR-6.2) is
written once, against one representation, not once per format.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class NodeKind(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"


@dataclass(frozen=True, slots=True)
class DocumentNode:
    kind: NodeKind
    text: str
    level: int | None
    """Heading level (1-6) for HEADING nodes; None otherwise."""
    page: int | None
    """1-indexed source page, when the format has pages (PDF); None for
    formats with no native pagination (DOCX/MD/HTML/TXT)."""
