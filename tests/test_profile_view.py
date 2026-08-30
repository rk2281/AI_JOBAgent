"""Tests for the pure /profile renderer in app.services.profile_view.

render_profile takes a ProfileSnapshot and returns a BotReply with no
database, bot token, or Gemini key involved — every branch here is a
decision about what a user sees for a given stored state, which is
exactly what a plain function call can check. ProfileService, the
thin layer that loads a ProfileSnapshot from Postgres, is not tested
here for the same reason extract_cv itself is not tested in
test_cv_extraction.py: it needs a real database.
"""

from __future__ import annotations

from app.services.profile_view import (
    ProfileSnapshot,
    _is_empty_profile,
    render_profile,
)

# -- has_user / has_cv gates -------------------------------------------------


def test_no_user_asks_for_start() -> None:
    reply = render_profile(ProfileSnapshot(has_user=False))

    assert "/start" in reply.text


def test_no_cv_asks_for_upload() -> None:
    reply = render_profile(ProfileSnapshot(has_user=True, has_cv=False))

    assert "PDF" in reply.text


# -- extraction_status branches ----------------------------------------------


def test_pending_says_not_read_yet() -> None:
    reply = render_profile(
        ProfileSnapshot(has_user=True, has_cv=True, extraction_status="pending")
    )

    assert "haven't read" in reply.text


def test_extracting_says_in_progress() -> None:
    reply = render_profile(
        ProfileSnapshot(has_user=True, has_cv=True, extraction_status="extracting")
    )

    assert "reading" in reply.text


def test_no_text_layer_suggests_text_pdf() -> None:
    reply = render_profile(
        ProfileSnapshot(has_user=True, has_cv=True, extraction_status="no_text_layer")
    )

    assert "scan" in reply.text


def test_failed_shows_the_real_reason() -> None:
    reply = render_profile(
        ProfileSnapshot(
            has_user=True,
            has_cv=True,
            extraction_status="failed",
            extraction_error="Gemini quota exceeded",
        )
    )

    assert "Gemini quota exceeded" in reply.text


def test_empty_status_is_not_reported_as_success() -> None:
    reply = render_profile(
        ProfileSnapshot(has_user=True, has_cv=True, extraction_status="empty")
    )

    assert "couldn't pull anything useful" in reply.text
    assert "Skills:" not in reply.text


def test_complete_but_empty_json_is_caught_too() -> None:
    """The pre-EMPTY legacy row: status=complete, but nothing was extracted."""
    reply = render_profile(
        ProfileSnapshot(
            has_user=True,
            has_cv=True,
            extraction_status="complete",
            extracted_profile={},
        )
    )

    assert "couldn't pull anything useful" in reply.text
    assert "Skills:" not in reply.text


def test_complete_with_only_target_roles_is_still_empty() -> None:
    reply = render_profile(
        ProfileSnapshot(
            has_user=True,
            has_cv=True,
            extraction_status="complete",
            extracted_profile={"target_roles": ["ML Engineer"]},
        )
    )

    assert "couldn't pull anything useful" in reply.text


# -- populated profile --------------------------------------------------


def test_populated_profile_shows_original_spellings() -> None:
    reply = render_profile(
        ProfileSnapshot(
            has_user=True,
            has_cv=True,
            extraction_status="complete",
            extracted_profile={
                "summary": "Engineer",
                "skills": ["PyTorch", "ANSYS Fluent"],
            },
        )
    )

    assert "PyTorch" in reply.text
    assert "ANSYS Fluent" in reply.text
    assert "pytorch" not in reply.text


def test_skills_are_truncated_with_a_count() -> None:
    skills = [f"Skill{i}" for i in range(20)]
    reply = render_profile(
        ProfileSnapshot(
            has_user=True,
            has_cv=True,
            extraction_status="complete",
            extracted_profile={"summary": "Engineer", "skills": skills},
        )
    )

    assert "(+8 more)" in reply.text


def test_experience_years_shown_to_one_decimal() -> None:
    reply = render_profile(
        ProfileSnapshot(
            has_user=True,
            has_cv=True,
            extraction_status="complete",
            extracted_profile={
                "summary": "Engineer",
                "total_experience_years": 1.6,
            },
        )
    )

    assert "1.6 years" in reply.text


def test_target_roles_are_labelled_as_a_guess() -> None:
    reply = render_profile(
        ProfileSnapshot(
            has_user=True,
            has_cv=True,
            extraction_status="complete",
            extracted_profile={
                "summary": "Engineer",
                "skills": ["Python"],
                "target_roles": ["MLE"],
            },
        )
    )

    assert "MLE" in reply.text
    assert "preferences win" in reply.text


def test_current_role_renders_as_present() -> None:
    reply = render_profile(
        ProfileSnapshot(
            has_user=True,
            has_cv=True,
            extraction_status="complete",
            extracted_profile={
                "summary": "Engineer",
                "experience": [
                    {
                        "title": "Backend Engineer",
                        "start_date": "Jan 2024",
                        "is_current": True,
                    }
                ],
            },
        )
    )

    assert "Jan 2024 – Present" in reply.text


# -- _is_empty_profile --------------------------------------------------


def test_is_empty_profile_true_for_none() -> None:
    assert _is_empty_profile(None) is True


def test_is_empty_profile_false_when_summary_only() -> None:
    assert _is_empty_profile({"summary": "Engineer"}) is False


# -- plain text rendering --------------------------------------------------


def test_skills_label_is_plain_text() -> None:
    reply = render_profile(
        ProfileSnapshot(
            has_user=True,
            has_cv=True,
            extraction_status="complete",
            extracted_profile={"summary": "Engineer", "skills": ["Python"]},
        )
    )

    assert "Skills:" in reply.text
