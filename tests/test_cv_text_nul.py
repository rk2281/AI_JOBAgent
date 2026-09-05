"""A CV text layer must never carry a NUL byte into PostgreSQL.

Found in production on 2026-09-05: a real CV's PDF text layer contained
`\x00` where the "+" of an international phone number should have been,
and `extract_cv` died with

    psycopg.DataError: PostgreSQL text fields cannot contain NUL (0x00) bytes

in its THIRD phase -- after the Gemini call had already been spent.

Nothing caught it because the whole suite fed the extractors clean
fixtures. A PDF text layer is not clean input: it is whatever the
font's encoding table produced, and an unmapped glyph comes back as
NUL.
"""

from __future__ import annotations

import io
import json

from docx import Document

from app.services.cv_text import extract_raw_text


def _docx_bytes(lines: list[str]) -> bytes:
    document = Document()
    for line in lines:
        document.add_paragraph(line)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_nul_bytes_are_removed_from_a_text_layer() -> None:
    """The exact shape seen in production."""
    from app.services.cv_text import _strip_nul

    dirty = "VARUN NARAD\nCybersecurity Engineer\n\x00918700430994"
    clean = _strip_nul(dirty, file_type="pdf")

    assert "\x00" not in clean
    assert "918700430994" in clean
    # Removed, not replaced with a space: the NUL sat inside the token,
    # so a space would split the phone number rather than repair it.
    assert clean.endswith("\n918700430994")


def test_text_without_nul_is_returned_unchanged() -> None:
    """The clean path must not be rewritten by the cleaner.

    `str.replace` on a string with no match returns an equal string,
    but asserting equality here is what would catch a future version
    of this function that also normalised whitespace, stripped accents,
    or did anything else nobody asked for.
    """
    from app.services.cv_text import _strip_nul

    original = "Priya Sharma\n\nSKILLS\nPython, SQL\t\u2014 five years"
    assert _strip_nul(original, file_type="docx") == original


def test_extraction_strips_nul_on_the_pdf_path(monkeypatch) -> None:
    """The wiring, on the path where this actually happens.

    A .docx CANNOT carry a NUL: python-docx refuses to write one
    ("All strings must be XML compatible"), which is itself the
    answer to "why was this only ever seen on a PDF". So the pdf
    extractor is stubbed to return what pypdf really returned, and
    what is under test is that `extract_raw_text` cleans it before
    handing it on.
    """
    import app.services.cv_text as cv_text

    monkeypatch.setattr(
        cv_text,
        "_extract_pdf_text",
        lambda data: "VARUN NARAD\nCybersecurity Engineer\n\x00918700430994",
    )

    text = cv_text.extract_raw_text("pdf", b"%PDF-1.4 not really")

    assert "\x00" not in text
    assert "918700430994" in text


def test_the_cleaned_text_is_json_serialisable_for_jsonb(monkeypatch) -> None:
    """JSONB rejects \\u0000 too, so the profile column has the same rule.

    The extracted profile is built by the model FROM this text, so
    cleaning here keeps a NUL out of `cv_versions.extracted_profile`
    as well as out of `cvs.raw_text`. One fix, both columns -- which is
    why it lives at the extraction boundary rather than at the write.
    """
    import app.services.cv_text as cv_text

    monkeypatch.setattr(cv_text, "_extract_pdf_text", lambda data: "A\x00B")

    text = cv_text.extract_raw_text("pdf", b"%PDF")

    assert "\\u0000" not in json.dumps({"summary": text})


def test_a_docx_cannot_carry_a_nul_in_the_first_place() -> None:
    """Records why the docx path was never the one that broke.

    Not a redundant test: it is the evidence for the scoping claim in
    the docstring above. If a future python-docx starts permitting
    NULs, this fails and the docx path needs the same attention the
    pdf path just got.
    """
    import pytest

    with pytest.raises(ValueError, match="XML compatible"):
        _docx_bytes(["Name\x00Surname"])
