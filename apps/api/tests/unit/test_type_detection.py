from __future__ import annotations

import io

import pytest

from aether.app.ingestion.type_detection import (
    DetectedDocumentType,
    UnsupportedDocumentTypeError,
    detect_document_type,
)

pytestmark = pytest.mark.unit

_PDF_HEADER = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\n"


def _real_docx_bytes() -> bytes:
    import docx

    doc = docx.Document()
    doc.add_paragraph("hello")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_pdf_is_detected_by_magic_bytes_regardless_of_declared_mime() -> None:
    assert (
        detect_document_type(_PDF_HEADER, declared_mime="application/octet-stream")
        == DetectedDocumentType.PDF
    )


def test_docx_is_detected_by_magic_bytes() -> None:
    assert (
        detect_document_type(_real_docx_bytes(), declared_mime="application/octet-stream")
        == DetectedDocumentType.DOCX
    )


def test_a_binary_masquerading_as_pdf_via_declared_mime_is_rejected() -> None:
    """The core anti-spoofing guarantee: an executable with a
    forged/mismatched declared content-type must not be accepted just
    because the uploader claimed it was a PDF."""
    fake_exe = b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 100  # PE/DOS executable header
    with pytest.raises(UnsupportedDocumentTypeError):
        detect_document_type(fake_exe, declared_mime="application/pdf")


def test_html_is_detected_by_content_signature() -> None:
    html = b"<!DOCTYPE html>\n<html><body><h1>Hi</h1></body></html>"
    assert detect_document_type(html, declared_mime="text/plain") == DetectedDocumentType.HTML


def test_html_signature_is_case_insensitive_and_tolerates_leading_whitespace() -> None:
    html = b"  \n<HTML><body>hi</body></html>"
    assert detect_document_type(html, declared_mime="text/plain") == DetectedDocumentType.HTML


def test_markdown_falls_back_to_declared_mime_since_it_has_no_magic_bytes() -> None:
    md = b"# Heading\n\nSome *markdown* text."
    assert detect_document_type(md, declared_mime="text/markdown") == DetectedDocumentType.MARKDOWN


def test_plain_text_falls_back_to_declared_mime() -> None:
    text = b"Just an ordinary paragraph of prose."
    assert detect_document_type(text, declared_mime="text/plain") == DetectedDocumentType.TEXT


def test_undecodable_binary_with_no_magic_bytes_is_rejected() -> None:
    garbage = bytes(range(256)) * 4  # not valid UTF-8, no recognized magic bytes
    with pytest.raises(UnsupportedDocumentTypeError):
        detect_document_type(garbage, declared_mime="text/plain")


def test_unsupported_declared_mime_for_plain_text_content_is_rejected() -> None:
    text = b"some content"
    with pytest.raises(UnsupportedDocumentTypeError):
        detect_document_type(text, declared_mime="application/x-unknown")
