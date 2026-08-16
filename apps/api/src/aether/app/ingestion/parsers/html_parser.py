"""HTML parser (§3.2.7, FR-KB-1). Walks h1-h6/p/table elements in
document order — real semantic structure, same posture as the DOCX
parser. Uses Python's built-in ``html.parser`` backend (no extra native
dependency like lxml's C library beyond what beautifulsoup4 already
pulls in) since untrusted, possibly-malformed HTML is exactly the kind
of input a lenient built-in parser handles without choking, and this
runs in the worker process, not the API, per TB-6's isolation posture
for parsers.
"""

from __future__ import annotations

from bs4 import BeautifulSoup
from bs4.element import Tag

from aether.app.ingestion.document_tree import DocumentNode, NodeKind

_HEADING_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6"]
_STRUCTURAL_TAGS = [*_HEADING_TAGS, "p", "table"]


class HtmlParseError(Exception):
    """The document produced no recognizable structural content at
    all — not a parser failure (BeautifulSoup itself never raises on
    malformed markup), but nothing usable came out of it."""


def parse_html(content: bytes) -> list[DocumentNode]:
    soup = BeautifulSoup(content.decode("utf-8", errors="replace"), "html.parser")

    nodes: list[DocumentNode] = []
    for element in soup.find_all(_STRUCTURAL_TAGS):
        if element.name == "table":
            node = _table_node(element)
        elif element.name == "p":
            if element.find_parent("table") is not None:
                continue  # already captured whole by the table's own node
            node = _paragraph_node(element)
        else:
            node = _heading_node(element)
        if node is not None:
            nodes.append(node)

    if not nodes:
        raise HtmlParseError("no extractable content (no h1-h6/p/table elements)")
    return nodes


def _heading_node(element: Tag) -> DocumentNode | None:
    text = element.get_text(strip=True)
    if not text:
        return None
    level = int(element.name.removeprefix("h"))
    return DocumentNode(kind=NodeKind.HEADING, text=text, level=level, page=None)


def _paragraph_node(element: Tag) -> DocumentNode | None:
    text = element.get_text(strip=True)
    if not text:
        return None
    return DocumentNode(kind=NodeKind.PARAGRAPH, text=text, level=None, page=None)


def _table_node(element: Tag) -> DocumentNode | None:
    rows: list[list[str]] = []
    for row in element.find_all("tr"):
        cells = [cell.get_text(strip=True) for cell in row.find_all(["td", "th"])]
        if any(cells):
            rows.append(cells)
    if not rows:
        return None
    text = "\n".join(" | ".join(row) for row in rows)
    return DocumentNode(kind=NodeKind.TABLE, text=text, level=None, page=None)
