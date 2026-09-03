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

import ast
from pathlib import Path

import pytest

from app.core.config import settings
from app.db.models.scoring import ScoringStatus
from app.services.job_scoring import (
    SKIP_CV_NOT_EMBEDDED,
    SKIP_NO_ACTIVE_CV,
    SKIP_NO_PROFILE,
    classify_skip_reason,
    is_notify_eligible,
    is_scorable_user,
    select_status,
)


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


# --- who can be scored at all -------------------------------------------
#
# The four states a user can be in, and the reason two of them are one
# test input rather than two: active_version_with_embedding() filters
# "no active version" and "active version with no embedding" in SQL, so
# both reach is_scorable_user() as embedded_version_present=False. Each
# is written out separately anyway, because the INTENT of the two cases
# differs -- only the third is fixable by running the embedding pass --
# and a test that records intent survives a refactor that a merged test
# would not.


def test_profile_absent_is_not_scorable() -> None:
    assert (
        is_scorable_user(profile_present=False, embedded_version_present=False)
        is False
    )


def test_profile_without_an_active_cv_version_is_not_scorable() -> None:
    """State 2: profiles.active_cv_version_id IS NULL."""
    assert (
        is_scorable_user(profile_present=True, embedded_version_present=False)
        is False
    )


def test_active_cv_version_without_an_embedding_is_not_scorable() -> None:
    """State 3: the version exists but cv_versions.embedding IS NULL.

    Indistinguishable from state 2 at this boundary, and that is the
    point being recorded: scoring cannot tell them apart either.
    """
    assert (
        is_scorable_user(profile_present=True, embedded_version_present=False)
        is False
    )


def test_profile_with_an_embedded_active_version_is_scorable() -> None:
    """State 4, the only scorable one."""
    assert (
        is_scorable_user(profile_present=True, embedded_version_present=True)
        is True
    )


def test_an_embedded_version_without_a_profile_is_not_scorable() -> None:
    """Cannot occur through the repository -- the query joins THROUGH
    profiles -- but the predicate must not treat it as scorable if it
    is ever called directly.
    """
    assert (
        is_scorable_user(profile_present=False, embedded_version_present=True)
        is False
    )


# --- the cross-check script must stay independent -----------------------


def test_scorable_targets_check_does_not_reference_the_shared_predicate() -> None:
    """scripts/scorable_targets_check.py is an ORACLE, not a caller.

    Its whole value is that it reaches the same answer by a route that
    shares no code with the thing it checks. The moment it imports
    is_scorable_user or select_target_user_ids, its agreement becomes
    circular -- two callers of one predicate always agree -- while it
    goes on printing "AGREE" exactly as before. That is the failure
    this test exists to make loud.

    A denylist, not a whitelist: the script is free to grow any other
    import it needs. Only these two names are forbidden.
    """
    forbidden = {"is_scorable_user", "select_target_user_ids"}
    path = Path(__file__).resolve().parents[1] / "scripts" / "scorable_targets_check.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))

    imported: set[str] = set()
    referenced: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imported.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.Attribute):
            referenced.add(node.attr)
        elif isinstance(node, ast.Name):
            referenced.add(node.id)

    assert not (imported & forbidden), "the oracle imports the predicate it checks"
    # Catches an attribute call that skipped the import, e.g.
    # job_scoring.is_scorable_user(...).
    assert not (referenced & forbidden), "the oracle references the predicate it checks"


# --- which of the three causes made a user unscorable -------------------
#
# is_scorable_user stays a function of exactly two booleans. This is
# reporting layered on top of it, reached only after it has already
# returned False, so nothing here can change who gets scored.


def test_no_profile_is_reported_as_no_profile() -> None:
    assert (
        classify_skip_reason(
            profile_present=False,
            active_version_present=False,
            embedded_version_present=False,
        )
        == SKIP_NO_PROFILE
    )


def test_a_profile_with_no_active_version_is_reported_as_no_active_cv() -> None:
    assert (
        classify_skip_reason(
            profile_present=True,
            active_version_present=False,
            embedded_version_present=False,
        )
        == SKIP_NO_ACTIVE_CV
    )


def test_an_active_version_without_an_embedding_is_reported_as_not_embedded() -> None:
    """The only one of the three that running the embedding pass fixes."""
    assert (
        classify_skip_reason(
            profile_present=True,
            active_version_present=True,
            embedded_version_present=False,
        )
        == SKIP_CV_NOT_EMBEDDED
    )


def test_the_deepest_cause_wins_at_the_exact_input_where_all_three_match() -> None:
    """The boundary, stated at the exact value rather than near it.

    A user with no profile row also has no active version and no
    embedded version, so every branch in classify_skip_reason matches.
    Order decides the answer, and the answer must be the one that sends
    somebody to fix the right thing: reporting "CV not embedded" for a
    user who never completed onboarding points at the wrong stage
    entirely, and running the embedding pass would change nothing.
    """
    reason = classify_skip_reason(
        profile_present=False,
        active_version_present=False,
        embedded_version_present=False,
    )
    assert reason == SKIP_NO_PROFILE
    assert reason != SKIP_NO_ACTIVE_CV
    assert reason != SKIP_CV_NOT_EMBEDDED


def test_a_profileless_user_who_somehow_has_an_active_version_is_still_no_profile() -> None:
    """Cannot happen through the repository -- the query joins THROUGH
    profiles -- but the ordering must not depend on that."""
    assert (
        classify_skip_reason(
            profile_present=False,
            active_version_present=True,
            embedded_version_present=False,
        )
        == SKIP_NO_PROFILE
    )


def test_classifying_a_scorable_user_raises_rather_than_returning_a_reason() -> None:
    """A caller reaching this with a scorable user means the branch
    above it is wrong. A plausible-looking string is exactly how that
    would go unnoticed: the counters would still sum, the funnel would
    still balance, and one scored user would be reported as skipped for
    a reason that never happened.
    """
    with pytest.raises(AssertionError):
        classify_skip_reason(
            profile_present=True,
            active_version_present=True,
            embedded_version_present=True,
        )


def test_the_scorable_case_is_exactly_the_case_is_scorable_user_accepts() -> None:
    """The two functions must disagree about nothing. Whatever
    is_scorable_user calls scorable is precisely what classify_skip_reason
    refuses to classify."""
    for profile_present in (True, False):
        for embedded_version_present in (True, False):
            scorable = is_scorable_user(
                profile_present=profile_present,
                embedded_version_present=embedded_version_present,
            )
            if not scorable:
                continue
            with pytest.raises(AssertionError):
                classify_skip_reason(
                    profile_present=profile_present,
                    active_version_present=True,
                    embedded_version_present=embedded_version_present,
                )


def test_the_three_skip_reasons_are_distinct_values() -> None:
    """Two sharing a value would let the breakdown sum to
    users_skipped_no_cv while reporting the wrong cause -- the funnel
    assertion would pass and the number a person reads would be wrong.
    """
    reasons = [SKIP_NO_PROFILE, SKIP_NO_ACTIVE_CV, SKIP_CV_NOT_EMBEDDED]
    assert len(set(reasons)) == 3


def test_the_three_skip_reasons_are_plain_strings() -> None:
    """Not an enum: these travel into a JSONB counters column and out to
    a script's stdout, and an enum would be re-serialised at both ends."""
    for reason in (SKIP_NO_PROFILE, SKIP_NO_ACTIVE_CV, SKIP_CV_NOT_EMBEDDED):
        assert type(reason) is str


def test_the_skip_breakdown_sums_to_the_counter_it_explains() -> None:
    """The arithmetic the fourth funnel assertion enforces, at the one
    shape that matters: three causes, one total."""
    no_profile, no_active_cv, not_embedded = 2, 1, 3
    skipped_no_cv = 6
    assert skipped_no_cv == no_profile + no_active_cv + not_embedded


def test_a_forgotten_counter_breaks_the_sum() -> None:
    """What the fourth assertion is for: a fourth skip cause added later
    whose counter nobody increments shows up as a breakdown quietly
    totalling less than the number above it."""
    no_profile, no_active_cv, not_embedded = 2, 1, 3
    skipped_no_cv_with_a_fourth_cause = 7
    assert skipped_no_cv_with_a_fourth_cause != no_profile + no_active_cv + not_embedded
