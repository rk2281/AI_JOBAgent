"""Tests for the Day 3 onboarding pieces.

These run without a database and without a Telegram token. That is
possible only because the parsing and validation logic lives in
services rather than in handlers — the point of the layering.
"""

from __future__ import annotations

import pytest

from app.db.models import Base
from app.db.models.user import OnboardingState
from app.services.cv_intake import CVIntakeService, CVValidationError
from app.services.onboarding import (
    EXPERIENCE_CHOICES,
    THRESHOLD_CHOICES,
    OnboardingService,
    parse_list_input,
)

MARKDOWN_SPECIAL_CHARACTERS = ("*", "_", "`", "[", "]")


# -- schema ---------------------------------------------------------------


def test_users_table_tracks_onboarding_state() -> None:
    column = Base.metadata.tables["users"].c.onboarding_state

    assert column.nullable is False
    assert "VARCHAR" in str(column.type).upper()


def test_onboarding_state_default_is_new() -> None:
    assert OnboardingState.NEW.value == "new"
    assert OnboardingState("complete") is OnboardingState.COMPLETE


def test_every_state_value_fits_the_column() -> None:
    limit = Base.metadata.tables["users"].c.onboarding_state.type.length

    for state in OnboardingState:
        assert len(state.value) <= limit


# -- Markdown safety --------------------------------------------------------


def test_prompts_contain_no_markdown_special_characters() -> None:
    """Prompts must stay plain text even though replies go out unparsed.

    A live run hit a Telegram 400 BadRequest because a reply containing
    a user's CV file name ("AI_ML Engineer Resume.pdf") was sent with
    parse_mode="Markdown" — the underscore in the file name was read as
    an unclosed italic marker. The fix was to stop parsing any reply
    that can contain user input as Markdown at all, rather than trying
    to escape user text. This guards the one piece that could quietly
    reintroduce the risk: if a prompt is ever sent with parse_mode set
    again, it must not contain characters Markdown treats specially.
    """
    service = OnboardingService(session=None)  # type: ignore[arg-type]

    for state in OnboardingState:
        reply = service._prompt_for(state)

        for character in MARKDOWN_SPECIAL_CHARACTERS:
            assert character not in reply.text, (
                f"{state}: prompt text contains Markdown character {character!r}"
            )

        for row in reply.buttons:
            for button in row:
                for character in MARKDOWN_SPECIAL_CHARACTERS:
                    assert character not in button.label, (
                        f"{state}: button label {button.label!r} contains "
                        f"Markdown character {character!r}"
                    )


# -- list parsing ---------------------------------------------------------


def test_parse_splits_on_commas_and_trims() -> None:
    assert parse_list_input("  Backend Engineer , ML Engineer ") == [
        "Backend Engineer",
        "ML Engineer",
    ]


def test_parse_splits_on_newlines_too() -> None:
    assert parse_list_input("Delhi\nNoida") == ["Delhi", "Noida"]


def test_parse_drops_case_insensitive_duplicates_keeping_first() -> None:
    assert parse_list_input("Backend Engineer, backend engineer") == [
        "Backend Engineer"
    ]


def test_parse_collapses_internal_whitespace() -> None:
    assert parse_list_input("Data    Scientist") == ["Data Scientist"]


def test_parse_returns_empty_for_junk() -> None:
    assert parse_list_input("   ,  , ") == []


def test_parse_caps_the_number_of_items() -> None:
    many = ", ".join(f"role{n}" for n in range(50))
    assert len(parse_list_input(many)) == 10


# -- choices --------------------------------------------------------------


def test_experience_brackets_are_ordered_and_open_ended_at_the_top() -> None:
    assert EXPERIENCE_CHOICES["0-1"] == (0, 1)
    assert EXPERIENCE_CHOICES["8+"] == (8, None)


def test_thresholds_are_within_zero_to_one() -> None:
    assert all(0.0 < value < 1.0 for value in THRESHOLD_CHOICES.values())


# -- CV validation --------------------------------------------------------


@pytest.fixture
def intake(tmp_path) -> CVIntakeService:
    return CVIntakeService(storage_dir=str(tmp_path))


def test_accepts_pdf_and_docx(intake: CVIntakeService) -> None:
    assert intake.validate_metadata("cv.pdf", 1000) == "pdf"
    assert intake.validate_metadata("CV.DOCX", 1000) == "docx"


def test_rejects_other_extensions(intake: CVIntakeService) -> None:
    with pytest.raises(CVValidationError):
        intake.validate_metadata("cv.txt", 1000)


def test_rejects_missing_filename(intake: CVIntakeService) -> None:
    with pytest.raises(CVValidationError):
        intake.validate_metadata(None, 1000)


def test_rejects_oversized_files(intake: CVIntakeService) -> None:
    with pytest.raises(CVValidationError):
        intake.validate_metadata("cv.pdf", 50 * 1024 * 1024)


def test_rejects_empty_files(intake: CVIntakeService) -> None:
    with pytest.raises(CVValidationError):
        intake.validate_metadata("cv.pdf", 0)


def test_rejects_content_that_does_not_match_extension(
    intake: CVIntakeService,
) -> None:
    with pytest.raises(CVValidationError):
        intake.validate_content("pdf", b"this is plain text, not a PDF")


def test_accepts_real_pdf_magic_bytes(intake: CVIntakeService) -> None:
    intake.validate_content("pdf", b"%PDF-1.7\n...")


def test_accepts_real_docx_magic_bytes(intake: CVIntakeService) -> None:
    intake.validate_content("docx", b"PK\x03\x04rest of the zip")


def test_stored_path_ignores_the_uploaded_filename(
    intake: CVIntakeService, tmp_path
) -> None:
    """A malicious file name must not escape the storage directory."""
    path = intake.build_path(user_id=7, file_type="pdf")

    assert path.parent == tmp_path / "7"
    assert path.suffix == ".pdf"
    assert ".." not in str(path)


def test_two_uploads_do_not_collide(intake: CVIntakeService) -> None:
    first = intake.build_path(user_id=7, file_type="pdf")
    second = intake.build_path(user_id=7, file_type="pdf")

    assert first != second


def test_save_writes_the_bytes(intake: CVIntakeService) -> None:
    stored = intake.save(user_id=3, file_type="pdf", data=b"%PDF-1.7 body")

    from pathlib import Path

    assert Path(stored.storage_path).read_bytes() == b"%PDF-1.7 body"
    assert stored.size_bytes == len(b"%PDF-1.7 body")


def test_delete_removes_the_file(intake: CVIntakeService) -> None:
    from pathlib import Path

    stored = intake.save(user_id=5, file_type="pdf", data=b"%PDF-1.7 body")

    intake.delete(user_id=5, storage_path=stored.storage_path)

    assert not Path(stored.storage_path).exists()


def test_delete_refuses_a_path_outside_the_storage_directory(
    intake: CVIntakeService, tmp_path
) -> None:
    outside = tmp_path.parent / "not_a_cv.pdf"
    outside.write_bytes(b"not a cv")

    with pytest.raises(ValueError):
        intake.delete(user_id=5, storage_path=str(outside))

    assert outside.exists()


def test_delete_is_a_noop_for_a_missing_file(
    intake: CVIntakeService, tmp_path
) -> None:
    missing = tmp_path / "5" / "does-not-exist.pdf"

    intake.delete(user_id=5, storage_path=str(missing))
