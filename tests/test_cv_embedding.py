"""Tests for the Day 7 CV embedding pass's pure pieces.

No database and no network. run_cv_embedding() itself opens real
sessions via session_scope() and is not exercised here -- the same
boundary tests/test_job_embedding.py draws around run_job_embedding().

What is worth checking here and cannot be checked on the jobs side:
CV text is long enough to actually hit the truncation budget, where a
job document (title + a 500-character excerpt) never can. That
difference is the whole reason cv_embedding.py's docstring calls out
`truncated` as "the first place this number will be non-zero."
"""

from __future__ import annotations

from app.core.config import settings
from app.services.cv_embedding import SCOPE_CV_VERSIONS
from app.services.embedding_text import build_cv_document, fit_to_budget
from app.services.job_embedding import SCOPE_JOBS

REALISTIC_PROFILE = {
    "current_title": "Machine Learning Engineer",
    "target_roles": ["AI Engineer", "ML Engineer"],
    "skills": ["Python", "PyTorch", "FastAPI", "SQL", "Docker"],
    "summary": "Builds and ships ML systems end to end, from data pipelines to production APIs.",
    "experience": [
        {
            "title": "ML Engineer",
            "company": "Acme Corp",
            "description": "Trained and deployed recommendation models serving millions of requests daily.",
        },
        {
            "title": "Backend Engineer",
            "company": "Widgets Inc",
            "description": "Built REST APIs and internal tooling for the data platform team.",
        },
    ],
}


def test_realistic_cv_document_is_non_empty_and_within_budget() -> None:
    document = build_cv_document(REALISTIC_PROFILE)
    assert document != ""
    assert len(document) < settings.embedding_max_chars


def test_a_long_cv_does_truncate_where_a_job_document_never_could() -> None:
    """A job document is a title plus a 500-character excerpt -- about
    550 characters against an 8000-character budget, so fit_to_budget
    can never trim it. A CV with several verbose roles and a long
    summary is the first realistic input that actually reaches the
    limit, which is exactly why cv_embedding.py's docstring singles
    this counter out: a non-zero `truncated` here is expected to
    happen sometimes, not a sign of a bug."""
    # build_cv_document caps each role description at 300 characters and
    # takes at most 5 roles, so neither alone can reach the budget --
    # the summary is uncapped there (fit_to_budget is what enforces the
    # limit) and is what has to carry this test over 8000 characters.
    long_role_description = "Led cross-functional initiatives spanning data engineering, ML infrastructure, and platform reliability. " * 20

    profile = {
        "current_title": "Principal Engineer",
        "summary": "Extensive experience across the full stack, from infrastructure to product. " * 150,
        "skills": ["Python", "Go", "Kubernetes", "PyTorch", "SQL", "AWS", "Terraform"],
        "experience": [
            {
                "title": f"Senior Engineer, Team {n}",
                "company": f"Company {n}",
                "description": long_role_description,
            }
            for n in range(5)
        ],
    }

    document = build_cv_document(profile)
    _, truncated = fit_to_budget(document)

    assert truncated is True


def test_cv_and_job_scopes_are_different_strings() -> None:
    """Both passes write into the same embedding_runs table, keyed
    apart only by `scope`. If these two ever collided, one pass's run
    history would silently merge into the other's."""
    assert SCOPE_CV_VERSIONS == "cv_versions"
    assert SCOPE_JOBS == "jobs"
    assert SCOPE_CV_VERSIONS != SCOPE_JOBS
