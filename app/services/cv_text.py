"""Extracting the raw text layer from an uploaded CV file.

Format-specific and deliberately dumb: it turns bytes into text and
nothing else. No LLM calls, no database access, no judgment about
whether the result is good enough to extract a profile from — that
call belongs to app.services.cv_extraction, which is the only caller.
"""

from __future__ import annotations

import io

from docx import Document
from pypdf import PdfReader


class UnsupportedCVFormat(Exception):
    """file_type is not one this module has an extractor for."""


def extract_raw_text(file_type: str, data: bytes) -> str:
    """Return the text layer of a CV file.

    An empty string, not an exception, is the signal for "no text
    layer" — a scanned PDF is a valid, expected outcome here, not a
    parsing failure. app.services.cv_extraction is the layer that
    decides an empty result means the CV needs NO_TEXT_LAYER rather
    than FAILED.
    """
    if file_type == "pdf":
        return _extract_pdf_text(data)
    if file_type == "docx":
        return _extract_docx_text(data)

    raise UnsupportedCVFormat(
        f"No text extractor registered for file_type={file_type!r}"
    )


def _extract_pdf_text(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(pages).strip()


def _extract_docx_text(data: bytes) -> str:
    document = Document(io.BytesIO(data))
    paragraphs = [paragraph.text for paragraph in document.paragraphs]
    return "\n".join(paragraphs).strip()
