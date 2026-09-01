"""Tests for the job and CV document builders.

No database, no API. These are the decisions about what a job and a CV
mean as text, and every one of them is checkable offline.
"""

from __future__ import annotations

from app.services.embedding_text import (
    build_cv_document,
    build_job_document,
    document_hash,
    fit_to_budget,
)

PROFILE = {
    "current_title": "Machine Learning Engineer",
    "target_roles": ["AI Engineer", "ML Engineer"],
    "skills": ["Python", "PyTorch", "FastAPI"],
    "summary": "Builds and ships ML systems.",
    "location": "Noida",
    "total_experience_years": 2.0,
    "education": [{"degree": "B.Tech", "institution": "Some University"}],
    "experience": [
        {
            "title": "ML Intern",
            "company": "Acme",
            "description": "Trained models.",
        }
    ],
}


def test_job_document_labels_both_fields() -> None:
    document = build_job_document("Data Scientist", "Build models.")
    assert "Job title: Data Scientist" in document
    assert "Description: Build models." in document


def test_job_document_excludes_company_and_location() -> None:
    """The Day 6 suggestion was title + company + location + description.
    Day 7 rejects the middle two, so the builder does not accept them at
    all -- a field that cannot be passed cannot be added by accident."""
    document = build_job_document("Data Scientist", "Work in Noida for Acme Staffing.")
    # Only what the description itself happens to say survives.
    assert "Job title" in document
    assert document.count("Job title") == 1


def test_job_document_survives_a_missing_description() -> None:
    document = build_job_document("Data Scientist", None)
    assert document == "Job title: Data Scientist"


def test_job_document_is_empty_when_there_is_nothing_to_embed() -> None:
    """Empty is a skip, not a failure. The caller must not send it."""
    assert build_job_document("   ", None) == ""
    assert build_job_document(None, "") == ""


def test_job_document_collapses_whitespace() -> None:
    document = build_job_document("Data   Scientist\n", "Build\t\tmodels.")
    assert "Job title: Data Scientist" in document
    assert "Description: Build models." in document


def test_cv_document_includes_the_fields_that_describe_the_work() -> None:
    document = build_cv_document(PROFILE)
    assert "Current role: Machine Learning Engineer" in document
    assert "AI Engineer" in document
    assert "PyTorch" in document
    assert "Builds and ships ML systems." in document
    assert "ML Intern at Acme" in document


def test_cv_document_excludes_the_deterministic_signals() -> None:
    """location and total_experience_years are Day 8's 15% and 20%
    signals, scored with exact rules. In the vector too, they would be
    counted twice, once badly."""
    document = build_cv_document(PROFILE)
    assert "Noida" not in document
    assert "2.0" not in document


def test_cv_document_excludes_education() -> None:
    """Institution names cluster the way company names do, without
    saying much about what work someone is for."""
    document = build_cv_document(PROFILE)
    assert "Some University" not in document
    assert "B.Tech" not in document


def test_cv_document_tolerates_a_malformed_stored_profile() -> None:
    """extracted_profile is JSONB written from a model's output. A row
    stored before a schema change is not guaranteed to match today's
    shape, and one odd row must not fail an entire pass."""
    assert build_cv_document(None) == ""
    assert build_cv_document({}) == ""
    assert build_cv_document({"skills": "not a list"}) == ""
    assert build_cv_document({"experience": ["not a dict"]}) == ""
    assert build_cv_document({"current_title": 12345}) == "Current role: 12345"


def test_cv_document_caps_the_experience_list() -> None:
    profile = {
        "experience": [
            {"title": f"Role {n}", "company": "Acme"} for n in range(20)
        ]
    }
    document = build_cv_document(profile)
    assert "Role 4" in document
    assert "Role 5" not in document


def test_fit_to_budget_leaves_short_text_alone() -> None:
    text, truncated = fit_to_budget("short", max_chars=100)
    assert text == "short"
    assert truncated is False


def test_fit_to_budget_at_exactly_the_boundary_does_not_truncate() -> None:
    """The Day 6 lesson. A check written with >= would trim one
    character off every document landing exactly on the limit and
    report a truncation that did not need to happen."""
    text = "a" * 50
    result, truncated = fit_to_budget(text, max_chars=50)
    assert result == text
    assert truncated is False


def test_fit_to_budget_one_character_over_does_truncate() -> None:
    result, truncated = fit_to_budget("a" * 51, max_chars=50)
    assert truncated is True
    assert len(result) <= 50


def test_fit_to_budget_reports_that_it_truncated() -> None:
    """Truncation must be a counted event rather than a silent one --
    a truncated document still produces a perfectly normal-looking
    vector describing half the input."""
    _, truncated = fit_to_budget("word " * 100, max_chars=50)
    assert truncated is True


def test_document_hash_is_stable_and_sensitive() -> None:
    assert document_hash("abc") == document_hash("abc")
    assert document_hash("abc") != document_hash("abd")
    assert len(document_hash("abc")) == 64
