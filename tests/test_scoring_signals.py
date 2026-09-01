"""Tests for the pure Day 8 matching signals.

Ordinary synchronous functions throughout. No asyncio, no fixtures,
no database -- pytest-asyncio is not installed here and nothing in
this module needs it.
"""

import pytest

from app.services.scoring_signals import (
    score_experience,
    score_location,
    score_skill,
    score_title,
    semantic_clamp_flags,
    score_semantic,
)


# --- score_semantic ----------------------------------------------------------


def test_semantic_none_input_abstains() -> None:
    result = score_semantic(None)
    assert result.value is None


def test_semantic_low_end_of_observed_range() -> None:
    result = score_semantic(0.5058)
    assert result.value == pytest.approx(0.0290, abs=1e-4)
    assert semantic_clamp_flags(0.5058) == (False, False)


def test_semantic_midpoint() -> None:
    result = score_semantic(0.62)
    assert result.value == pytest.approx(0.6000, abs=1e-4)
    assert semantic_clamp_flags(0.62) == (False, False)


def test_semantic_high_end_of_observed_range() -> None:
    result = score_semantic(0.6928)
    assert result.value == pytest.approx(0.9640, abs=1e-4)
    assert semantic_clamp_flags(0.6928) == (False, False)


def test_semantic_upper_anchor_is_a_boundary_not_a_clamp() -> None:
    """raw == anchor_high (0.70) maps to 1.0 by plain arithmetic and
    must NOT be reported as clamped -- (high-low)/(high-low) is 1.0
    on its own, so semantic_clamp_flags must use strict `>`."""
    result = score_semantic(0.70)
    assert result.value == pytest.approx(1.0000, abs=1e-4)
    assert semantic_clamp_flags(0.70) == (False, False)


def test_semantic_lower_anchor_is_a_boundary_not_a_clamp() -> None:
    """raw == anchor_low (0.50) maps to 0.0 by plain arithmetic and
    must NOT be reported as clamped, for the same reason as the upper
    anchor boundary: semantic_clamp_flags must use strict `<`."""
    result = score_semantic(0.50)
    assert result.value == pytest.approx(0.0000, abs=1e-4)
    assert semantic_clamp_flags(0.50) == (False, False)


def test_semantic_above_upper_anchor_clamps_high() -> None:
    result = score_semantic(0.75)
    assert result.value == pytest.approx(1.0000, abs=1e-4)
    assert semantic_clamp_flags(0.75) == (False, True)


def test_semantic_below_lower_anchor_clamps_low() -> None:
    result = score_semantic(0.40)
    assert result.value == pytest.approx(0.0000, abs=1e-4)
    assert semantic_clamp_flags(0.40) == (True, False)


# --- score_skill ---------------------------------------------------------------


def test_skill_empty_job_list_abstains() -> None:
    assert score_skill([], ["python"]).value is None


def test_skill_empty_profile_list_abstains() -> None:
    assert score_skill(["python"], []).value is None


def test_skill_full_overlap() -> None:
    assert score_skill(["python", "fastapi"], ["python", "fastapi"]).value == 1.0


def test_skill_half_overlap() -> None:
    assert score_skill(["python", "fastapi"], ["python"]).value == 0.5


def test_skill_no_overlap() -> None:
    assert score_skill(["python"], ["java"]).value == 0.0


def test_skill_job_side_duplicates_are_deduplicated() -> None:
    assert score_skill(["python", "python"], ["python"]).value == 1.0


def test_skill_denominator_is_job_side_not_profile_side() -> None:
    result = score_skill(["a", "b", "c", "d"], ["a", "b", "c", "d", "e", "f"])
    assert result.value == 1.0


# --- score_experience ----------------------------------------------------------


def test_experience_unknown_candidate_abstains() -> None:
    assert score_experience(None, 2, 4).value is None


def test_experience_job_states_no_requirement_abstains() -> None:
    assert score_experience(3.0, None, None).value is None


def test_experience_within_range() -> None:
    assert score_experience(3.0, 2, 4).value == 1.0


def test_experience_exactly_at_minimum_is_a_boundary_scoring_full() -> None:
    """x == job_min must score exactly 1.0 -- the range is inclusive
    at its lower edge, tested at exactly that value."""
    assert score_experience(2.0, 2, 4).value == 1.0


def test_experience_exactly_at_maximum_is_a_boundary_scoring_full() -> None:
    """x == job_max must score exactly 1.0 -- the range is inclusive
    at its upper edge, tested at exactly that value."""
    assert score_experience(4.0, 2, 4).value == 1.0


def test_experience_overqualified_scores_full_not_penalised() -> None:
    assert score_experience(5.0, 2, 4).value == 1.0


def test_experience_tapers_below_minimum() -> None:
    assert score_experience(1.5, 2, 4).value == pytest.approx(0.8333, abs=1e-4)


def test_experience_tapers_further_below_minimum() -> None:
    assert score_experience(0.0, 2, 4).value == pytest.approx(0.3333, abs=1e-4)


def test_experience_floors_at_zero_when_far_short() -> None:
    assert score_experience(0.0, 5, None).value == 0.0


def test_experience_open_ended_maximum_is_infinity() -> None:
    assert score_experience(6.0, 5, None).value == 1.0


def test_experience_missing_minimum_is_treated_as_zero() -> None:
    assert score_experience(3.0, None, 5).value == 1.0


def test_experience_genuine_fresher_fits_a_fresher_role() -> None:
    assert score_experience(0.0, 0, 2).value == 1.0


# --- score_location --------------------------------------------------------------


def test_location_direct_match() -> None:
    assert score_location("Noida", None, ["Noida"], False).value == 1.0


def test_location_alias_match() -> None:
    assert score_location("Gurugram", None, ["Gurgaon"], False).value == 1.0


def test_location_display_name_tail_is_stripped_before_alias_match() -> None:
    assert score_location("Bangalore, Karnataka", None, ["Bengaluru"], False).value == 1.0


def test_location_no_match() -> None:
    assert score_location("Mumbai", None, ["Noida"], False).value == 0.0


def test_location_remote_role_wins_regardless_of_city() -> None:
    assert score_location("Mumbai", "remote", ["Noida"], False).value == 1.0


def test_location_hybrid_in_different_city_is_half() -> None:
    assert score_location("Mumbai", "hybrid", ["Noida"], False).value == 0.5


def test_location_hybrid_in_matching_city_is_full() -> None:
    assert score_location("Noida", "hybrid", ["Noida"], False).value == 1.0


def test_location_country_only_abstains() -> None:
    assert score_location("India", None, ["Noida"], False).value is None


def test_location_unknown_job_location_abstains() -> None:
    assert score_location(None, None, ["Noida"], False).value is None


def test_location_no_preference_set_abstains() -> None:
    assert score_location("Noida", None, [], False).value is None


def test_location_remote_only_preference_rejects_non_remote() -> None:
    assert score_location("Noida", None, ["Noida"], True).value == 0.0


def test_location_remote_only_preference_accepts_remote() -> None:
    assert score_location("Mumbai", "remote", ["Noida"], True).value == 1.0


# --- score_title -----------------------------------------------------------------


def test_title_exact_match() -> None:
    result = score_title("Machine Learning Engineer", ["Machine Learning Engineer"])
    assert result.value == 1.0


def test_title_both_sides_generic_abstains() -> None:
    assert score_title("Senior Engineer", ["Lead Developer"]).value is None


def test_title_no_overlap() -> None:
    assert score_title("Machine Learning Engineer", ["Data Scientist"]).value == 0.0


def test_title_partial_overlap_after_stripping_weak_tokens() -> None:
    result = score_title("Senior Machine Learning Engineer", ["Machine Learning"])
    assert result.value == 1.0


def test_title_no_target_roles_abstains() -> None:
    assert score_title("ML Engineer", []).value is None


def test_title_generic_job_title_abstains() -> None:
    assert score_title("Engineer", ["Machine Learning"]).value is None


def test_title_uses_max_across_roles_not_mean() -> None:
    result = score_title("Data Engineer", ["Machine Learning", "Data Engineer"])
    assert result.value == 1.0
