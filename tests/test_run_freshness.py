"""Tests for scripts/check_run_freshness.py.

`assess()` is pure so the window can be tested AT its boundary rather
than near it. CLAUDE.md section 2: a threshold written with `<` misses
the boundary, and here the boundary is the ordinary case -- a nightly
run is always almost exactly one period old.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scripts.check_run_freshness import (
    DEFAULT_MAX_AGE_HOURS,
    EMPTY,
    EXIT_ERROR,
    FRESH,
    STALE,
    assess,
)

NOW = datetime(2026, 9, 5, 6, 0, tzinfo=timezone.utc)


def _assess(hours_ago: float | None, *, finished: bool = True, total: int = 1):
    started = None if hours_ago is None else NOW - timedelta(hours=hours_ago)
    return assess(
        newest_started_at=started,
        newest_finished_at=(
            (started + timedelta(minutes=5)) if (started and finished) else None
        ),
        total_runs=0 if hours_ago is None else total,
        unfinished_runs=0 if finished else 1,
        now=NOW,
        max_age_hours=DEFAULT_MAX_AGE_HOURS,
    )


def test_a_run_from_last_night_is_fresh() -> None:
    assert _assess(4).state is FRESH


def test_exactly_at_the_window_is_fresh() -> None:
    """The boundary QUALIFIES. Written with `<=` on purpose.

    A nightly run is always almost exactly one period old, so the
    boundary is not an edge case here -- it is the normal case, and a
    strict comparison would produce an alert most mornings.
    """
    assert _assess(DEFAULT_MAX_AGE_HOURS).state is FRESH


def test_one_second_past_the_window_is_stale() -> None:
    assert _assess(DEFAULT_MAX_AGE_HOURS + (1 / 3600)).state is STALE


def test_a_missed_night_is_stale() -> None:
    assert _assess(30).state is STALE


def test_no_rows_at_all_is_empty_not_stale() -> None:
    """A new deployment and a dead scheduler must not read the same.

    Only one of them is an incident, and telling somebody to check a
    scheduled task that was never registered wastes the hour in which
    the real problem is still happening.
    """
    verdict = _assess(None)
    assert verdict.state is EMPTY
    assert verdict.age_hours is None


def test_the_three_states_have_three_exit_codes() -> None:
    assert _assess(4).exit_code == 0
    assert _assess(30).exit_code == 1
    assert _assess(None).exit_code == 2
    # And "could not tell" is none of them.
    assert EXIT_ERROR not in {0, 1, 2}


def test_a_run_that_started_but_never_finished_is_still_fresh() -> None:
    """Freshness asks whether the scheduler FIRED, not whether it worked.

    A run killed mid-enrichment leaves finished_at NULL, and that row
    is evidence the task ran. Reporting it as stale would send somebody
    to Task Scheduler when the problem is in the pipeline. The
    unfinished count is carried alongside so the two are distinguishable
    in the output.
    """
    verdict = _assess(4, finished=False)
    assert verdict.state is FRESH
    assert verdict.newest_finished_at is None
    assert verdict.unfinished_runs == 1


def test_the_window_is_wider_than_the_nightly_period() -> None:
    """26 > 24, and the gap is what absorbs a delayed start.

    Pinned as a test because a future edit to DEFAULT_MAX_AGE_HOURS
    that drops it to 24 would produce an alert on most mornings, and
    an alert that fires when nothing is wrong is an alert that gets
    switched off.
    """
    assert DEFAULT_MAX_AGE_HOURS > 24
