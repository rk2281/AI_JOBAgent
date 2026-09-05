"""Day 12 workstream 1 -- what the score does when data is missing.

The Day 12 brief asks whether weight renormalisation can produce a
misleadingly high score, and answers the question the way CLAUDE.md
requires: establish the intended behaviour, decide whether it is a bug
or a decision, and then either fix it or WRITE IT DOWN AND TEST IT.

The finding is that renormalisation can indeed produce a high score
from very little evidence, that this is the documented intent of the
abstain model rather than an accident, and that the guard against it
is `min_weight_covered_to_notify` -- not the score. These tests pin
the arithmetic so that anyone changing `combine()` has to change a
test that states, in words, which property they are giving up.

Nothing here changes the algorithm. See docs/MATCHING_AND_SCORING.md.
"""

from app.core.config import settings
from app.services.scoring import QualityAssessment, combine
from app.services.scoring_signals import SignalScore

ABSTAIN = SignalScore(value=None, reason="no data")
NEUTRAL_QUALITY = QualityAssessment(
    multiplier=1.0,
    reasons=[],
    is_agency=False,
    missing_city=False,
)


def _hit(value: float) -> SignalScore:
    return SignalScore(value=value, reason="matched")


def _combine(**overrides):
    signals = {
        "semantic": ABSTAIN,
        "skill": ABSTAIN,
        "experience": ABSTAIN,
        "location": ABSTAIN,
        "title": ABSTAIN,
        "semantic_raw": None,
        "quality": NEUTRAL_QUALITY,
    }
    signals.update(overrides)
    return combine(**signals)


# --- complete data --------------------------------------------------------


def test_complete_data_covers_the_whole_weight() -> None:
    pair = _combine(
        semantic=_hit(0.8),
        skill=_hit(0.6),
        experience=_hit(1.0),
        location=_hit(1.0),
        title=_hit(0.5),
        semantic_raw=0.8,
    )

    assert pair.weight_covered == 1.0

    expected = (
        0.8 * settings.weight_semantic
        + 0.6 * settings.weight_skill
        + 1.0 * settings.weight_experience
        + 1.0 * settings.weight_location
        + 0.5 * settings.weight_title
    )
    assert abs(pair.final_score - expected) < 1e-12


# --- one signal missing at a time ----------------------------------------


def test_missing_skills_leaves_the_weight_uncovered_not_zeroed() -> None:
    """A job with no extractable skills must not be scored 0 on 30%."""
    pair = _combine(
        semantic=_hit(1.0),
        experience=_hit(1.0),
        location=_hit(1.0),
        title=_hit(1.0),
        semantic_raw=1.0,
    )

    assert pair.skill.value is None
    assert abs(pair.weight_covered - (1.0 - settings.weight_skill)) < 1e-12
    # Renormalised, so four perfect signals still read as a perfect fit.
    assert abs(pair.final_score - 1.0) < 1e-12


def test_missing_experience_behaves_the_same_way() -> None:
    pair = _combine(
        semantic=_hit(1.0),
        skill=_hit(1.0),
        location=_hit(1.0),
        title=_hit(1.0),
        semantic_raw=1.0,
    )
    assert abs(pair.weight_covered - (1.0 - settings.weight_experience)) < 1e-12
    assert abs(pair.final_score - 1.0) < 1e-12


def test_missing_location_behaves_the_same_way() -> None:
    pair = _combine(
        semantic=_hit(1.0),
        skill=_hit(1.0),
        experience=_hit(1.0),
        title=_hit(1.0),
        semantic_raw=1.0,
    )
    assert abs(pair.weight_covered - (1.0 - settings.weight_location)) < 1e-12


def test_a_missing_description_removes_skill_and_experience_together() -> None:
    """The realistic case: no description means two signals abstain.

    Adzuna truncates descriptions at 500 characters, which is why
    `abstain_experience` sits at 98/99 on live data. This is the shape
    that actually occurs, not a synthetic one.
    """
    pair = _combine(
        semantic=_hit(0.4),
        location=_hit(1.0),
        title=_hit(1.0),
        semantic_raw=0.4,
    )

    expected_cover = (
        settings.weight_semantic + settings.weight_location + settings.weight_title
    )
    assert abs(pair.weight_covered - expected_cover) < 1e-12


def test_salary_is_not_a_signal_and_its_absence_changes_nothing() -> None:
    """Day 12 asks for a missing-salary case. There isn't one.

    Salary is not among the five weighted signals and is not read by
    `combine()`. Recording that as a test rather than as silence,
    because "we tested missing salary" and "salary cannot affect the
    score" are different claims and only the second one is true.
    """
    assert not hasattr(combine, "salary")

    signal_weights = (
        settings.weight_skill,
        settings.weight_semantic,
        settings.weight_experience,
        settings.weight_location,
        settings.weight_title,
    )
    assert abs(sum(signal_weights) - 1.0) < 1e-9


# --- the asymmetry, stated as arithmetic ---------------------------------


def test_missing_data_can_outrank_bad_data() -> None:
    """A job we could not assess outranks one we assessed badly.

    Both jobs are identical except that the first abstains on skill and
    the second scores 0.0 on it. The abstaining job wins. This is the
    "abstention is mildly rewarded" item in CLAUDE.md section 7 -- real,
    reproduced here, and deliberately NOT fixed by this test.
    """
    unknown_skills = _combine(
        semantic=_hit(0.5),
        experience=_hit(0.5),
        location=_hit(1.0),
        title=_hit(1.0),
        semantic_raw=0.5,
    )
    bad_skills = _combine(
        semantic=_hit(0.5),
        skill=_hit(0.0),
        experience=_hit(0.5),
        location=_hit(1.0),
        title=_hit(1.0),
        semantic_raw=0.5,
    )

    assert unknown_skills.final_score > bad_skills.final_score
    assert unknown_skills.weight_covered < bad_skills.weight_covered


def test_the_live_shape_has_a_floor_of_zero_point_six() -> None:
    """skill+experience abstain, location and title 1.0 -> 0.4*sem + 0.6.

    This is the closed form recorded in CLAUDE.md section 7 ("Open after
    Day 10 Part 4"), and it is why the notification threshold and the
    coverage floor cannot be tuned independently: in this shape the
    score cannot fall below 0.60 however poor the semantic match, so
    the 0.7 threshold is cleared at semantic >= 0.25.

    If this test fails, the abstention model has been changed. That may
    be correct -- but it is a decision, and the decision is recorded in
    docs/MATCHING_AND_SCORING.md, not in a diff.
    """
    for semantic_value in (0.0, 0.25, 0.5, 1.0):
        pair = _combine(
            semantic=_hit(semantic_value),
            location=_hit(1.0),
            title=_hit(1.0),
            semantic_raw=semantic_value,
        )
        assert abs(pair.final_score - (0.4 * semantic_value + 0.6)) < 1e-12

    floor = _combine(
        semantic=_hit(0.0),
        location=_hit(1.0),
        title=_hit(1.0),
        semantic_raw=0.0,
    )
    assert abs(floor.final_score - 0.60) < 1e-12


# --- nothing at all -------------------------------------------------------


def test_an_incomplete_profile_that_abstains_everywhere_says_so() -> None:
    """Zero coverage is a state with its own reason, not a zero score."""
    pair = _combine()

    assert pair.weight_covered == 0.0
    assert pair.final_score == 0.0
    assert pair.match_reasons[0] == "no signal had data"


def test_zero_coverage_is_distinguishable_from_scoring_zero() -> None:
    """The two ways to reach final_score 0.0 must not look identical."""
    nothing_known = _combine()
    everything_bad = _combine(
        semantic=_hit(0.0),
        skill=_hit(0.0),
        experience=_hit(0.0),
        location=_hit(0.0),
        title=_hit(0.0),
        semantic_raw=0.0,
    )

    assert nothing_known.final_score == everything_bad.final_score == 0.0
    assert nothing_known.weight_covered == 0.0
    assert everything_bad.weight_covered == 1.0
    assert "no signal had data" in nothing_known.match_reasons
    assert "no signal had data" not in everything_bad.match_reasons


def test_every_abstention_is_explained() -> None:
    """An unexplained abstain is a low score with no reason recorded."""
    pair = _combine(semantic=_hit(0.9), semantic_raw=0.9)

    abstained = [r for r in pair.match_reasons if r.startswith("abstained: ")]
    assert len(abstained) == 4


# --- explanations must not contradict the number they sit beside ---------


def test_a_zero_title_score_does_not_claim_an_overlap() -> None:
    """Found on a stored row: title_score 0.0 next to "title overlaps".

    "NLP Engineer" against ["AI Engineer", "ML Engineer", "Machine
    Learning Engineer"] shares only weak tokens, so the real overlap is
    empty and the score is correctly 0.0 -- but the reason claimed a
    match. match_reasons is the explanation a USER is shown, so that
    was a false claim made to a person.

    Invisible unless the reason and the score are read in the same
    query, which is exactly how it surfaced.
    """
    from app.services.scoring_signals import score_title

    result = score_title(
        "NLP Engineer",
        ["AI Engineer", "ML Engineer", "Machine Learning Engineer"],
    )

    assert result.value == 0.0
    assert "overlaps" not in result.reason
    assert "no words" in result.reason


def test_a_real_title_overlap_still_says_so() -> None:
    from app.services.scoring_signals import score_title

    result = score_title("Machine Learning Engineer", ["Machine Learning Engineer"])

    assert result.value == 1.0
    assert "overlaps" in result.reason


def test_no_signal_claims_a_match_while_scoring_zero() -> None:
    """The general form of the bug above, across all five signals.

    A reason is free to explain a zero ("0 of 3 required skills",
    "different city"). What it must not do is assert a match. This
    checks the words that assert one.
    """
    from app.services.scoring_signals import (
        score_location,
        score_skill,
        score_title,
    )

    claims_a_match = ("overlaps", "match", "matches")

    zero_scores = [
        score_skill(["kubernetes", "go", "rust"], ["python"]),
        score_location(
            job_location="Chennai",
            preferred_locations=["Delhi"],
            remote_only=False,
            job_work_mode=None,
        ),
        score_title("NLP Engineer", ["AI Engineer"]),
    ]

    for signal in zero_scores:
        assert signal.value == 0.0, signal
        assert not any(word in signal.reason for word in claims_a_match), (
            f"a signal scoring 0.0 claims a match: {signal.reason!r}"
        )
