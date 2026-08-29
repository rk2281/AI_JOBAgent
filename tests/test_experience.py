"""Tests for app.services.experience.compute_total_experience_years.

Every test pins today to a fixed date rather than reading the real
clock — a test that depends on the current month would change its
expected answer every month it kept passing.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.schemas.cv_profile import CVProfile, ExperienceEntry
from app.services.experience import compute_total_experience_years

TODAY = datetime(2026, 8, 1, tzinfo=UTC)


def entry(**kwargs: object) -> ExperienceEntry:
    kwargs.setdefault("title", "Engineer")
    kwargs.setdefault("company", "Acme")
    return ExperienceEntry(**kwargs)  # type: ignore[arg-type]


# -- compute_total_experience_years --------------------------------------


def test_empty_list_is_unknown() -> None:
    assert compute_total_experience_years([], today=TODAY) is None


def test_only_free_text_dates_is_unknown() -> None:
    """No structured start_year means the entry can't be placed in time."""
    entries = [entry(start_date="Jan 2022", end_date="Present", is_current=True)]

    assert compute_total_experience_years(entries, today=TODAY) is None


def test_full_year_role_counts_both_endpoints() -> None:
    entries = [entry(start_year=2024, start_month=1, end_year=2024, end_month=12)]

    assert compute_total_experience_years(entries, today=TODAY) == 1.0


def test_single_month_role_is_not_zero() -> None:
    entries = [entry(start_year=2024, start_month=6, end_year=2024, end_month=6)]

    assert compute_total_experience_years(entries, today=TODAY) == 0.1


def test_year_only_range_fills_january_to_december() -> None:
    entries = [entry(start_year=2022, end_year=2023)]

    assert compute_total_experience_years(entries, today=TODAY) == 2.0


def test_is_current_counts_up_to_today() -> None:
    entries = [entry(start_year=2026, start_month=2, is_current=True)]

    assert compute_total_experience_years(entries, today=TODAY) == 0.6


def test_missing_end_without_is_current_is_unknown() -> None:
    """end_year is None on its own is ambiguous, not "still there"."""
    entries = [entry(start_year=2024, start_month=1)]

    assert compute_total_experience_years(entries, today=TODAY) is None


def test_overlapping_identical_roles_are_unioned_not_summed() -> None:
    entries = [
        entry(start_year=2024, start_month=1, end_year=2024, end_month=12),
        entry(start_year=2024, start_month=1, end_year=2024, end_month=12),
    ]

    assert compute_total_experience_years(entries, today=TODAY) == 1.0


def test_overlapping_roles_merge_into_union() -> None:
    entries = [
        entry(start_year=2024, start_month=1, end_year=2024, end_month=12),
        entry(start_year=2024, start_month=7, end_year=2025, end_month=6),
    ]

    assert compute_total_experience_years(entries, today=TODAY) == 1.5


def test_roles_with_a_gap_are_not_bridged() -> None:
    entries = [
        entry(start_year=2022, start_month=1, end_year=2022, end_month=6),
        entry(start_year=2023, start_month=7, end_year=2023, end_month=12),
    ]

    assert compute_total_experience_years(entries, today=TODAY) == 1.0


def test_adjacent_roles_are_merged() -> None:
    entries = [
        entry(start_year=2023, start_month=1, end_year=2023, end_month=12),
        entry(start_year=2024, start_month=1, end_year=2024, end_month=12),
    ]

    assert compute_total_experience_years(entries, today=TODAY) == 2.0


def test_reversed_range_is_dropped_not_subtracted() -> None:
    entries = [
        entry(start_year=2024, start_month=1, end_year=2024, end_month=12),
        entry(start_year=2024, start_month=6, end_year=2024, end_month=1),
    ]

    assert compute_total_experience_years(entries, today=TODAY) == 1.0


def test_undated_role_is_dropped_not_counted() -> None:
    entries = [
        entry(start_year=2024, start_month=1, end_year=2024, end_month=12),
        entry(),
    ]

    assert compute_total_experience_years(entries, today=TODAY) == 1.0


def test_three_roles_with_overlap_gap_and_current() -> None:
    entries = [
        entry(start_year=2026, start_month=1, is_current=True),
        entry(start_year=2024, start_month=7, end_year=2025, end_month=2),
        entry(start_year=2024, start_month=4, end_year=2024, end_month=6),
    ]

    assert compute_total_experience_years(entries, today=TODAY) == 1.6


# -- schema ---------------------------------------------------------------


def test_experience_entry_structured_dates_default_to_none() -> None:
    result = entry()

    assert result.start_year is None
    assert result.start_month is None
    assert result.end_year is None
    assert result.end_month is None
    assert result.is_current is False


def test_experience_entry_keeps_free_text_alongside_structured_year() -> None:
    result = entry(start_date="Jan 2022", start_year=2022, start_month=1)

    assert result.start_date == "Jan 2022"
    assert result.start_year == 2022


def test_cv_profile_target_roles_defaults_to_empty_list() -> None:
    assert CVProfile().target_roles == []


def test_cv_profile_target_roles_round_trips_through_json_dump() -> None:
    profile = CVProfile(target_roles=["Backend Engineer", "Platform Engineer"])

    dumped = profile.model_dump(mode="json")

    assert dumped["target_roles"] == ["Backend Engineer", "Platform Engineer"]
