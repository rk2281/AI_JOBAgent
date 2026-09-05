"""Extracting the raw text layer from an uploaded CV file.

Format-specific and deliberately dumb: it turns bytes into text and
nothing else. No LLM calls, no database access, no judgment about
whether the result is good enough to extract a profile from — that
call belongs to app.services.cv_extraction, which is the only caller.
"""

from __future__ import annotations

import io
import logging

from docx import Document
from pypdf import PdfReader

logger = logging.getLogger(__name__)


class UnsupportedCVFormat(Exception):
    """file_type is not one this module has an extractor for."""


def _strip_nul(text: str, *, file_type: str) -> str:
    """Remove NUL (0x00) bytes. PostgreSQL text columns reject them.

    WHY THIS IS HERE AND NOT AT THE DATABASE

    A NUL is not content. It is what a PDF text layer produces when a
    glyph has no mapping in the font's encoding table -- observed on a
    real CV where the "+" of an international phone number came back as
    `\\x00918700430994`. No CV means to contain one.

    PostgreSQL refuses a NUL in a `text` or `varchar` column outright,
    and in JSONB too, so the failure is not subtle: `psycopg.DataError:
    PostgreSQL text fields cannot contain NUL (0x00) bytes`. Before
    Day 12 that exception surfaced from `extract_cv`'s third phase,
    AFTER the Gemini call had been paid for, and took the whole
    extraction down.

    Cleaning HERE rather than at the write is the point. This function
    returns the text that goes BOTH to the model and to the database,
    so the two cannot disagree. Sanitising only on the way to storage
    would mean `cvs.raw_text` was not the text the profile was
    extracted from -- and `embedding_source_hash` would be computed
    over one string while another was stored.

    Removed, not replaced with a space. A NUL stands for a glyph that
    failed to map, and in every observed case it sat inside a token
    rather than between two, so a space would split a phone number
    rather than repair one. The count is logged because a CV that
    needed cleaning is worth knowing about -- the count only, never
    the text, which is a candidate's personal data.
    """
    count = text.count("\x00")
    if not count:
        return text

    logger.info(
        "Stripped %d NUL byte(s) from a %s text layer before storage",
        count,
        file_type,
    )
    return text.replace("\x00", "")


def extract_raw_text(file_type: str, data: bytes) -> str:
    """Return the text layer of a CV file.

    An empty string, not an exception, is the signal for "no text
    layer" — a scanned PDF is a valid, expected outcome here, not a
    parsing failure. app.services.cv_extraction is the layer that
    decides an empty result means the CV needs NO_TEXT_LAYER rather
    than FAILED.
    """
    if file_type == "pdf":
        return _strip_nul(_extract_pdf_text(data), file_type=file_type)
    if file_type == "docx":
        return _strip_nul(_extract_docx_text(data), file_type=file_type)

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
