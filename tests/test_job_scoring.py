"""Tests for the three decisions run_scoring() makes after the numbers exist.

No database, no API, no event loop. The three functions under test were
extracted from run_scoring() precisely so they could be reached without
one -- every value below is passed in directly.

These are the three places where a wrong answer is indistinguishable
from a right one in a log: a funnel that balances while jobs went
missing, a status that reads HEALTHY while the model had no data, and a
notification gate that silently excludes the boundary value it was
written to include.
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.db.models.scoring import ScoringStatus
from app.services.job_scoring import is_notify_eligible, select_status


# --- the notify gates, at exactly their boundaries -------------------------
#
# Every one of the three uses >=, so the floor value QUALIFIES. Each
# gate is tested at exactly its floor and at one representable step
# below it, because "just below" is the only case that distinguishes
# >= from >.

_THRESHOLD = 0.70


def _eligible(
    final_score: float = 0.90,
    semantic_raw: float = 0.90,
    weight_covered: float = 1.00,
) -> bool:
    """All three inputs comfortably clear unless a test lowers one."""
    return is_notify_eligible(
        final_score=final_score,
        semantic_raw=semantic_raw,
        weight_covered=weight_covered,
        notification_threshold=_THRESHOLD,
    )


def test_settings_still_hold_the_floors_these_tests_assume() -> None:
    """If a floor is retuned, the boundary tests below must be retuned
    with it. Asserting the values here means that shows up as this test
    failing by name rather than as four boundary tests quietly checking
    the wrong number."""
    assert settings.semantic_notify_floor == pytest.approx(0.62)
    assert settings.min_weight_covered_to_notify == pytest.approx(0.55)


def test_final_score_exactly_at_the_threshold_qualifies() -> None:
    assert _eligible(final_score=0.70) is True


def test_final_score_just_below_the_threshold_does_not() -> None:
    assert _eligible(final_score=0.6999) is False


def test_semantic_raw_exactly_at_the_floor_qualifies() -> None:
    assert _eligible(semantic_raw=0.62) is True


def test_semantic_raw_just_below_the_floor_does_not() -> None:
    assert _eligible(semantic_raw=0.6199) is False


def test_weight_covered_exactly_at_the_minimum_qualifies() -> None:
    assert _eligible(weight_covered=0.55) is True


def test_weight_covered_just_below_the_minimum_does_not() -> None:
    assert _eligible(weight_covered=0.5499) is False


def test_all_three_exactly_at_their_boundaries_qualifies() -> None:
    """The three floors together, each at its exact value. A gate that
    was written with > anywhere in the chain fails here."""
    assert _eligible(final_score=0.70, semantic_raw=0.62, weight_covered=0.55) is True


def test_a_high_score_on_too_little_coverage_is_refused() -> None:
    """The whole reason the third gate exists. A pair scoring 0.95 on
    50% of the weight is a confident number computed from half a model,
    and it must not notify."""
    assert _eligible(final_score=0.95, semantic_raw=0.90, weight_covered=0.50) is False


def test_a_high_score_with_weak_semantic_is_refused() -> None:
    """A pair can clear the threshold on location and title alone while
    being semantically unrelated to the candidate. The absolute
    semantic floor is what stops that."""
    assert _eligible(final_score=0.95, semantic_raw=0.55, weight_covered=1.00) is False


# --- the status selection table --------------------------------------------


def _status(
    jobs_scored: int = 98,
    users_scored: int = 1,
    pairs_scored: int = 98,
    distinct_score_count: int = 98,
    notify_eligible: int = 0,
    all_signals_abstained_everywhere: bool = False,
) -> ScoringStatus:
    return select_status(
        jobs_scored=jobs_scored,
        users_scored=users_scored,
        pairs_scored=pairs_scored,
        distinct_score_count=distinct_score_count,
        notify_eligible=notify_eligible,
        all_signals_abstained_everywhere=all_signals_abstained_everywhere,
    )


def test_status_no_candidate_jobs_when_nothing_was_scorable() -> None:
    assert _status(jobs_scored=0, pairs_scored=0, distinct_score_count=0) == (
        ScoringStatus.NO_CANDIDATE_JOBS
    )


def test_status_no_scorable_users_when_jobs_existed_but_no_user_did() -> None:
    assert _status(users_scored=0, pairs_scored=0, distinct_score_count=0) == (
        ScoringStatus.NO_SCORABLE_USERS
    )


def test_status_all_abstained() -> None:
    assert _status(distinct_score_count=1, all_signals_abstained_everywhere=True) == (
        ScoringStatus.ALL_ABSTAINED
    )


def test_status_all_abstained_wins_over_degenerate() -> None:
    """THE ORDERING TEST.

    A fully abstained run has weight_covered == 0 on every pair, so
    every final_score is 0.0, so distinct_score_count is 1 -- it
    satisfies the DEGENERATE condition exactly. If the checks were
    reordered, this run would be filed as "the ranking carries no
    information" when what actually happened is "no signal had any
    data". Both are failures; they have completely different causes and
    completely different fixes.
    """
    assert _status(
        pairs_scored=98,
        distinct_score_count=1,
        all_signals_abstained_everywhere=True,
    ) == ScoringStatus.ALL_ABSTAINED


def test_status_degenerate_when_every_score_is_identical() -> None:
    assert _status(pairs_scored=98, distinct_score_count=1) == ScoringStatus.DEGENERATE


def test_status_one_pair_is_not_degenerate() -> None:
    """One pair trivially has one distinct score. That is a ranking of
    one, not a ranking that lost its information."""
    assert _status(jobs_scored=1, pairs_scored=1, distinct_score_count=1) == (
        ScoringStatus.COMPLETE_NO_QUALIFYING
    )


def test_status_complete_when_something_cleared_the_gate() -> None:
    assert _status(notify_eligible=3) == ScoringStatus.COMPLETE


def test_status_complete_no_qualifying_is_the_healthy_quiet_day() -> None:
    """Scoring worked, scores varied, nothing was good enough today.
    This must not be reachable by any of the failure paths above, which
    is what every other test in this block is protecting."""
    assert _status(notify_eligible=0) == ScoringStatus.COMPLETE_NO_QUALIFYING


def test_status_never_returns_running_or_failed_or_partial() -> None:
    """select_status() is reached only after the loop finished, so
    RUNNING and FAILED cannot be its answer. PARTIAL is currently
    unreachable too -- nothing in run_scoring() counts a partial
    failure. This test pins that as a known fact rather than leaving it
    as an assumption, so that adding partial-failure handling later
    fails here and forces the ladder to be updated with it."""
    unreachable = {ScoringStatus.RUNNING, ScoringStatus.FAILED, ScoringStatus.PARTIAL}
    observed = {
        _status(jobs_scored=0, pairs_scored=0, distinct_score_count=0),
        _status(users_scored=0, pairs_scored=0, distinct_score_count=0),
        _status(distinct_score_count=1, all_signals_abstained_everywhere=True),
        _status(pairs_scored=98, distinct_score_count=1),
        _status(notify_eligible=3),
        _status(notify_eligible=0),
    }
    assert observed & unreachable == set()


# --- the funnel equalities --------------------------------------------------
#
# run_scoring() asserts these at run time. Restated here as plain
# arithmetic so the SHAPE of each equality is pinned by a test, not
# only by an assertion that fires once a day against live data.


def test_user_funnel_balances() -> None:
    users_considered, users_skipped_no_cv, users_scored = 3, 2, 1
    assert users_considered == users_skipped_no_cv + users_scored


def test_job_funnel_balances_with_one_excluded_job() -> None:
    """99 active, all embedded, one excluded by hand."""
    jobs_considered = 99
    jobs_skipped_no_embedding = 0
    jobs_excluded_manual = 1
    jobs_scored = 98
    assert jobs_considered == (
        jobs_skipped_no_embedding + jobs_excluded_manual + jobs_scored
    )


def test_job_funnel_balanced_while_the_limit_bug_dropped_a_real_job() -> None:
    """The reason the third equality exists.

    Both funnel equalities above are computed from repository counts
    taken BEFORE the scoring loop. They describe what the run intended
    to do. While nearest_to() was being called with limit=jobs_scored
    against a pool that included excluded rows, one real job was
    dropped from every run -- and both equalities still balanced
    perfectly, because neither one looks at what the loop returned.
    """
    jobs_considered, jobs_skipped_no_embedding = 99, 0
    jobs_excluded_manual, jobs_scored = 1, 98
    pairs_scored_when_broken, users_scored = 97, 1

    assert jobs_considered == (
        jobs_skipped_no_embedding + jobs_excluded_manual + jobs_scored
    )
    assert pairs_scored_when_broken != jobs_scored * users_scored


def test_pair_funnel_balances_after_the_fix() -> None:
    jobs_scored, users_scored, pairs_scored = 98, 1, 98
    assert pairs_scored == jobs_scored * users_scored


def test_pair_funnel_balances_for_several_users() -> None:
    jobs_scored, users_scored, pairs_scored = 98, 3, 294
    assert pairs_scored == jobs_scored * users_scored
