"""Magic-byte type detection (§3.2.7: "type validation by magic bytes,
never extension"). The threat model is a malicious *binary* masquerading
as a benign format via a spoofed filename/declared content-type — magic
bytes exist precisely for the formats where that's exploitable (PDF,
Office Open XML). Plain-text formats (TXT/MD/HTML) have no magic bytes
by definition; the security-relevant question for them isn't "is this
secretly a PDF" (magic bytes would catch that) but "is this HTML being
mislabeled as inert text" — a real XSS/injection-adjacent concern
covered separately at render time (untrusted-content handling, not this
function's job). Distinguishing Markdown from plain prose is not a
security boundary at all: both are inert text parsed as text either way.
"""

from __future__ import annotations

import re
from enum import StrEnum

import filetype

_HTML_SIGNATURE = re.compile(rb"<!doctype\s+html|<html[\s>]", re.IGNORECASE)


class DetectedDocumentType(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    HTML = "html"
    MARKDOWN = "markdown"
    TEXT = "text"


class UnsupportedDocumentTypeError(Exception):
    """Neither the content's magic bytes nor its declared MIME type
    resolve to a supported format — or, for a format with real magic
    bytes, the declared MIME type contradicts what the bytes actually
    are (a spoofed-extension attack, or simple corruption)."""


_MAGIC_MIME_TO_TYPE = {
    "application/pdf": DetectedDocumentType.PDF,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (
        DetectedDocumentType.DOCX
    ),
}

_DECLARED_MIME_TO_TYPE = {
    "text/html": DetectedDocumentType.HTML,
    "text/markdown": DetectedDocumentType.MARKDOWN,
    "text/plain": DetectedDocumentType.TEXT,
}


def detect_document_type(content: bytes, *, declared_mime: str) -> DetectedDocumentType:
    magic_mime = filetype.guess_mime(content)
    if magic_mime is not None:
        # Real magic bytes were found — authoritative, overriding
        # whatever mime type was declared at upload time. A mismatch
        # here (magic bytes say X, nothing in our supported set matches)
        # means the file isn't one of the formats FR-KB-1 promises to
        # support, regardless of what its declared type claimed.
        detected = _MAGIC_MIME_TO_TYPE.get(magic_mime)
        if detected is None:
            raise UnsupportedDocumentTypeError(
                f"content's actual type ({magic_mime}) is not a supported format"
            )
        return detected

    # No magic bytes matched — plausibly plain text. Confirm it's
    # actually decodable text (a binary format filetype doesn't
    # recognize is not "plain text by elimination") before trusting the
    # declared type for the TEXT/MARKDOWN distinction it alone can make.
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnsupportedDocumentTypeError(
            "content has no recognized magic bytes and is not valid UTF-8 text"
        ) from exc

    if _HTML_SIGNATURE.search(content[:1024]):
        return DetectedDocumentType.HTML

    detected = _DECLARED_MIME_TO_TYPE.get(declared_mime)
    if detected is None:
        raise UnsupportedDocumentTypeError(
            f"declared type {declared_mime!r} is not a supported plain-text format"
        )
    if detected is DetectedDocumentType.HTML and "<" not in text:
        # Declared as HTML but contains no markup at all and didn't
        # match the HTML signature above — treat as plain text rather
        # than parsing an empty/malformed HTML document.
        return DetectedDocumentType.TEXT
    return detected
