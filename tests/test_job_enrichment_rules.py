"""Tests for the pure Day 8 enrichment rules.

Ordinary synchronous functions throughout -- no asyncio, no fixtures,
no database. pytest-asyncio is not installed in this project and
nothing here needs it.
"""

from app.services.job_enrichment_rules import (
    filter_and_normalize_skills,
    infer_work_mode,
)


# --- filter_and_normalize_skills --------------------------------------------


def test_plain_technologies_are_kept_and_normalized() -> None:
    result = filter_and_normalize_skills(["Python", "PyTorch"])
    assert result.kept == ["python", "pytorch"]
    assert result.dropped_too_long == []
    assert result.dropped_soft == []


def test_activity_phrase_is_dropped_as_too_long() -> None:
    result = filter_and_normalize_skills(["deploying models to production"])
    assert result.kept == []
    assert result.dropped_too_long == ["deploying models to production"]
    assert result.dropped_soft == []


def test_soft_skill_phrase_is_dropped() -> None:
    result = filter_and_normalize_skills(["Strong communication skills"])
    assert result.kept == []
    assert result.dropped_too_long == []
    assert result.dropped_soft == ["Strong communication skills"]


def test_exactly_three_words_is_kept_not_dropped() -> None:
    """The MAX_SKILL_WORDS boundary: comparison is `> 3`, not `>= 3`.

    "Amazon Web Services" is exactly three words and is a real skills
    catalog entry. Writing the check as `>= MAX_SKILL_WORDS` would
    drop it, which is the exact boundary bug this project keeps
    finding -- a threshold that fails on the case that looks like it
    should pass.
    """
    result = filter_and_normalize_skills(["Amazon Web Services"])
    assert result.kept == ["amazon web services"]
    assert result.dropped_too_long == []
    assert result.dropped_soft == []


def test_lossy_normalization_deduplicates() -> None:
    result = filter_and_normalize_skills(["Node.js", "NodeJS"])
    assert result.kept == ["nodejs"]
    assert result.dropped_too_long == []
    assert result.dropped_soft == []


def test_punctuation_aliases_are_applied() -> None:
    result = filter_and_normalize_skills(["C++", "C#", ".NET"])
    assert result.kept == ["cpp", "csharp", "dotnet"]
    assert result.dropped_too_long == []
    assert result.dropped_soft == []


def test_blanks_are_dropped_without_being_counted_anywhere() -> None:
    result = filter_and_normalize_skills(["", "   ", "Python"])
    assert result.kept == ["python"]
    assert result.dropped_too_long == []
    assert result.dropped_soft == []


def test_empty_input_produces_empty_result() -> None:
    result = filter_and_normalize_skills([])
    assert result.kept == []
    assert result.dropped_too_long == []
    assert result.dropped_soft == []


def test_live_stage_c_list_keeps_seven_technologies_and_drops_one_soft() -> None:
    raw = [
        "Python",
        "FastAPI",
        "PostgreSQL",
        "Redis",
        "Kubernetes",
        "Docker",
        "CI/CD",
        "Strong communication skills",
    ]
    result = filter_and_normalize_skills(raw)
    assert len(result.kept) == 7
    assert result.dropped_too_long == []
    assert result.dropped_soft == ["Strong communication skills"]


# --- infer_work_mode ---------------------------------------------------------


def test_remote_from_description() -> None:
    assert infer_work_mode("Engineer", "Noida", "Fully remote position.") == "remote"


def test_remote_from_location() -> None:
    assert infer_work_mode("Engineer", "Remote", "Join us.") == "remote"


def test_hybrid_wins_over_remote_when_both_present() -> None:
    """Precedence test: this description contains BOTH "hybrid" and
    "remote", and hybrid must win because it is checked first."""
    assert (
        infer_work_mode("Engineer", "Pune", "Hybrid - 3 days remote per week.")
        == "hybrid"
    )


def test_hybrid_from_description() -> None:
    assert infer_work_mode("Engineer", "Pune", "This is a hybrid role.") == "hybrid"


def test_remote_from_work_from_home_phrase() -> None:
    assert (
        infer_work_mode("Engineer", "Noida", "Work from home available.") == "remote"
    )


def test_remote_from_wfh_abbreviation() -> None:
    assert infer_work_mode("Engineer", "Noida", "WFH friendly.") == "remote"


def test_none_when_no_terms_present() -> None:
    assert infer_work_mode("Engineer", "Noida", "Great team, great benefits.") is None


def test_none_with_no_description() -> None:
    assert infer_work_mode("Engineer", "India", None) is None


def test_none_when_all_fields_are_none() -> None:
    assert infer_work_mode(None, None, None) is None


def test_whole_word_boundary_does_not_match_remotely() -> None:
    """`in` would return "remote" here because "remote" is a substring
    of "remotely-managed". A word-boundary regex must return None."""
    assert infer_work_mode("Engineer", "Noida", "Our remotely-managed fleet.") is None
