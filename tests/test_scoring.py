"""Tests for combining the five Day 8 signals into one traceable score.

Ordinary synchronous functions. No asyncio, no fixtures, no database.
"""

import pytest

from app.services.scoring import assess_quality, combine, rank
from app.services.scoring_signals import SignalScore


# --- assess_quality ------------------------------------------------------------


def test_quality_agency_by_exact_normalized_match() -> None:
    result = assess_quality("Weekday AI", "Noida")
    assert result.multiplier == pytest.approx(0.90)
    assert result.is_agency is True
    assert result.missing_city is False


def test_quality_agency_match_is_case_insensitive() -> None:
    result = assess_quality("weekday ai", "Noida")
    assert result.multiplier == pytest.approx(0.90)
    assert result.is_agency is True
    assert result.missing_city is False


def test_quality_both_penalties_multiply_together() -> None:
    result = assess_quality("Vrinda International", "India")
    assert result.multiplier == pytest.approx(0.855)
    assert result.is_agency is True
    assert result.missing_city is True


def test_quality_no_penalty_for_a_real_named_employer_with_a_city() -> None:
    result = assess_quality("HSBC", "Noida")
    assert result.multiplier == pytest.approx(1.0)
    assert result.is_agency is False
    assert result.missing_city is False


def test_quality_missing_city_penalty_alone() -> None:
    result = assess_quality("HSBC", "India")
    assert result.multiplier == pytest.approx(0.95)
    assert result.is_agency is False
    assert result.missing_city is True


def test_quality_company_match_is_exact_not_substring() -> None:
    """"Metadata Solutions" contains no agency name as a whole word,
    and must not be penalised just because "meta" is a substring of
    "metadata". Substring matching would fire here; exact equality
    correctly does not."""
    result = assess_quality("Metadata Solutions", "Noida")
    assert result.multiplier == pytest.approx(1.0)
    assert result.is_agency is False
    assert result.missing_city is False


def test_quality_missing_company_and_location() -> None:
    result = assess_quality(None, None)
    assert result.multiplier == pytest.approx(0.95)
    assert result.is_agency is False
    assert result.missing_city is True


# --- combine ---------------------------------------------------------------------


FULL_QUALITY = assess_quality("HSBC", "Noida")


def test_combine_all_signals_present_and_perfect() -> None:
    result = combine(
        semantic=SignalScore(1.0, "semantic"),
        skill=SignalScore(1.0, "skill"),
        experience=SignalScore(1.0, "experience"),
        location=SignalScore(1.0, "location"),
        title=SignalScore(1.0, "title"),
        semantic_raw=0.70,
        quality=FULL_QUALITY,
    )
    assert result.weight_covered == pytest.approx(1.0)
    assert result.weighted_total == pytest.approx(1.0)
    assert result.final_score == pytest.approx(1.0)
    assert result.match_reasons


def test_combine_skill_abstains_job_is_not_penalised() -> None:
    """A job with no extractable skills abstains on that 30% signal
    rather than being dragged down for a data gap."""
    result = combine(
        semantic=SignalScore(1.0, "semantic"),
        skill=SignalScore(None, "job lists no extractable skills"),
        experience=SignalScore(1.0, "experience"),
        location=SignalScore(1.0, "location"),
        title=SignalScore(1.0, "title"),
        semantic_raw=0.70,
        quality=FULL_QUALITY,
    )
    assert result.weight_covered == pytest.approx(0.70)
    assert result.weighted_total == pytest.approx(1.0)
    assert result.final_score == pytest.approx(1.0)
    assert result.match_reasons


def test_combine_skill_zero_proves_abstain_and_zero_differ() -> None:
    """Unlike the abstain case above, a skill score of a genuine 0.0
    DOES lower the total -- this is the pair that proves the two are
    different states, not two spellings of the same thing."""
    result = combine(
        semantic=SignalScore(1.0, "semantic"),
        skill=SignalScore(0.0, "0 of 3 required skills"),
        experience=SignalScore(1.0, "experience"),
        location=SignalScore(1.0, "location"),
        title=SignalScore(1.0, "title"),
        semantic_raw=0.70,
        quality=FULL_QUALITY,
    )
    assert result.weight_covered == pytest.approx(1.0)
    assert result.weighted_total == pytest.approx(0.70)
    assert result.match_reasons


def test_combine_all_signals_abstain() -> None:
    result = combine(
        semantic=SignalScore(None, "no embedding on one side"),
        skill=SignalScore(None, "job lists no extractable skills"),
        experience=SignalScore(None, "candidate experience unknown"),
        location=SignalScore(None, "no location preference set"),
        title=SignalScore(None, "no target roles set"),
        semantic_raw=None,
        quality=FULL_QUALITY,
    )
    assert result.weight_covered == pytest.approx(0.0)
    assert result.final_score == pytest.approx(0.0)
    assert result.match_reasons[0] == "no signal had data"
    assert result.match_reasons


def test_combine_applies_quality_multiplier_to_the_final_score() -> None:
    quality = assess_quality("Vrinda International", "India")
    result = combine(
        semantic=SignalScore(1.0, "semantic"),
        skill=SignalScore(1.0, "skill"),
        experience=SignalScore(1.0, "experience"),
        location=SignalScore(1.0, "location"),
        title=SignalScore(1.0, "title"),
        semantic_raw=0.70,
        quality=quality,
    )
    assert result.final_score == pytest.approx(0.855)
    assert result.match_reasons


# --- rank --------------------------------------------------------------------------


def _pair(final_score: float) -> object:
    return combine(
        semantic=SignalScore(final_score, "semantic"),
        skill=SignalScore(None, "job lists no extractable skills"),
        experience=SignalScore(None, "candidate experience unknown"),
        location=SignalScore(None, "no location preference set"),
        title=SignalScore(None, "no target roles set"),
        semantic_raw=None,
        quality=assess_quality("HSBC", "Noida"),
    )


def test_rank_orders_by_final_score_descending() -> None:
    pairs = [(1, _pair(0.9)), (2, _pair(0.5)), (3, _pair(0.7))]
    ranked = rank(pairs)
    ranks_by_job_id = {job_id: position for job_id, _, position in ranked}
    assert ranks_by_job_id[1] == 1
    assert ranks_by_job_id[3] == 2
    assert ranks_by_job_id[2] == 3


def test_rank_ties_are_broken_by_job_id_and_are_stable_across_calls() -> None:
    pairs = [(2, _pair(0.5)), (1, _pair(0.5))]
    first = rank(pairs)
    second = rank(pairs)

    first_by_job_id = {job_id: position for job_id, _, position in first}
    second_by_job_id = {job_id: position for job_id, _, position in second}

    assert first_by_job_id == {1: 1, 2: 2}
    assert first_by_job_id == second_by_job_id
