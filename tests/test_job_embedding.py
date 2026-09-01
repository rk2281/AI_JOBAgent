"""Tests for the Day 7 job embedding funnel and status classification.

No database and no network. run_job_embedding() itself opens real
sessions via session_scope() and is not exercised here -- what is
pure, and therefore checkable offline, is _classify(), _Counters and
_chunks(), which is exactly what this file covers.

pytest-asyncio is not installed in this project (see
tests/test_job_ingestion.py's _run() helper for the established
pattern of driving a coroutine with asyncio.run() instead). None of
that is needed here: every function under test -- _classify,
_Counters.accounted_for, _chunks, and EmbeddingResult.is_healthy -- is
a plain synchronous function or property.
"""

from __future__ import annotations

import pytest

from app.db.models.embedding import EmbeddingStatus
from app.services.job_embedding import EmbeddingResult, _Counters, _chunks, _classify


# -- _classify: one test per row of the table -------------------------------


def test_everything_already_current_is_nothing_to_do() -> None:
    counters = _Counters(
        candidates_considered=0,
        skipped_empty_text=0,
        attempted=0,
        succeeded=0,
        failed=0,
    )
    assert _classify(counters, total_in_scope=99) is EmbeddingStatus.NOTHING_TO_DO


def test_no_rows_in_the_table_at_all_is_no_source_rows() -> None:
    counters = _Counters(
        candidates_considered=0,
        skipped_empty_text=0,
        attempted=0,
        succeeded=0,
        failed=0,
    )
    assert _classify(counters, total_in_scope=0) is EmbeddingStatus.NO_SOURCE_ROWS


def test_every_candidate_had_empty_text_is_all_failed() -> None:
    counters = _Counters(
        candidates_considered=5,
        skipped_empty_text=5,
        attempted=0,
        succeeded=0,
        failed=0,
    )
    assert _classify(counters, total_in_scope=99) is EmbeddingStatus.ALL_FAILED


def test_all_attempted_rows_failed_is_all_failed() -> None:
    counters = _Counters(
        candidates_considered=5,
        skipped_empty_text=0,
        attempted=5,
        succeeded=0,
        failed=5,
    )
    assert _classify(counters, total_in_scope=99) is EmbeddingStatus.ALL_FAILED


def test_some_succeeded_some_failed_is_partial() -> None:
    counters = _Counters(
        candidates_considered=5,
        skipped_empty_text=0,
        attempted=5,
        succeeded=3,
        failed=2,
    )
    assert _classify(counters, total_in_scope=99) is EmbeddingStatus.PARTIAL


def test_everything_succeeded_is_complete() -> None:
    counters = _Counters(
        candidates_considered=99,
        skipped_empty_text=0,
        attempted=99,
        succeeded=99,
        failed=0,
    )
    assert _classify(counters, total_in_scope=99) is EmbeddingStatus.COMPLETE


def test_one_skipped_rest_fine_is_complete() -> None:
    counters = _Counters(
        candidates_considered=99,
        skipped_empty_text=1,
        attempted=98,
        succeeded=98,
        failed=0,
    )
    assert _classify(counters, total_in_scope=99) is EmbeddingStatus.COMPLETE


# -- NOTHING_TO_DO vs NO_SOURCE_ROWS -----------------------------------------


def test_nothing_to_do_and_no_source_rows_are_not_the_same_status() -> None:
    """Both mean zero rows embedded, with identical counters, and they
    mean opposite things: the work is done, versus there was nothing to
    work on. A table that should hold 99 active jobs reporting
    NO_SOURCE_ROWS means ingestion or the eligibility filter is broken;
    reporting NOTHING_TO_DO means the pass is healthy. Collapsing them
    into one status would erase that distinction."""
    counters = _Counters()

    healthy = _classify(counters, total_in_scope=99)
    broken = _classify(counters, total_in_scope=0)

    assert healthy is EmbeddingStatus.NOTHING_TO_DO
    assert broken is EmbeddingStatus.NO_SOURCE_ROWS
    assert healthy != broken


# -- _Counters.accounted_for() ------------------------------------------


@pytest.mark.parametrize(
    "counters",
    [
        _Counters(0, 0, 0, 0, 0),
        _Counters(0, 0, 0, 0, 0),
        _Counters(5, 5, 0, 0, 0),
        _Counters(5, 0, 5, 0, 5),
        _Counters(5, 0, 5, 3, 2),
        _Counters(99, 0, 99, 99, 0),
        _Counters(99, 1, 98, 98, 0),
    ],
)
def test_accounted_for_is_true_for_every_balanced_funnel(counters: _Counters) -> None:
    assert counters.accounted_for() is True


def test_accounted_for_is_false_when_a_row_is_lost_between_selection_and_sending() -> None:
    """candidates_considered=5 but skipped_empty_text + attempted = 4:
    one candidate vanished before it was either skipped or sent."""
    counters = _Counters(candidates_considered=5, skipped_empty_text=1, attempted=3)
    assert counters.accounted_for() is False


def test_accounted_for_is_false_when_attempted_does_not_match_outcomes() -> None:
    """attempted=5 but succeeded + failed = 4: one attempt never
    resolved to either outcome."""
    counters = _Counters(attempted=5, succeeded=3, failed=1)
    assert counters.accounted_for() is False


# -- EmbeddingResult.is_healthy -------------------------------------------


@pytest.mark.parametrize("status", list(EmbeddingStatus))
def test_is_healthy_true_only_for_complete_and_nothing_to_do(
    status: EmbeddingStatus,
) -> None:
    result = EmbeddingResult(status=status)
    assert result.is_healthy == (
        status in (EmbeddingStatus.COMPLETE, EmbeddingStatus.NOTHING_TO_DO)
    )


# -- _chunks --------------------------------------------------------------


def test_chunks_splits_99_items_into_13_chunks_of_8_with_a_remainder_of_3() -> None:
    items = list(range(99))
    chunks = _chunks(items, 8)

    assert len(chunks) == 13
    assert len(chunks[-1]) == 3


def test_chunks_flattening_preserves_original_order() -> None:
    """Order is what makes zip(batch, vectors, strict=True) safe back
    in run_job_embedding: if chunking reordered anything, a vector
    would be written to the wrong job."""
    items = list(range(99))
    chunks = _chunks(items, 8)

    flattened = [item for chunk in chunks for item in chunk]
    assert flattened == items


def test_chunks_of_an_empty_list_is_empty() -> None:
    assert _chunks([], 8) == []


# -- abandoned (Part 5: the live run exposed a gap the funnel had no name for) --


def test_abandoned_counts_candidates_never_sent() -> None:
    """Run 2 from the live report: 99 candidates, 72 attempted before
    the quota aborted the run. The 27 that were selected but never
    reached a batch are `abandoned`, and the funnel still balances --
    an aborted run legitimately leaves rows untouched."""
    counters = _Counters(
        candidates_considered=99,
        skipped_empty_text=0,
        attempted=72,
        succeeded=72,
        failed=0,
    )
    assert counters.abandoned == 27
    assert counters.accounted_for() is True


def test_abandoned_counts_a_run_that_never_got_past_the_first_batch() -> None:
    """Run 1 from the live report: the very first batch hit the quota
    before any row was resolved, so all 8 candidates are abandoned."""
    counters = _Counters(candidates_considered=8, skipped_empty_text=0, attempted=0)
    assert counters.abandoned == 8
    assert counters.accounted_for() is True


def test_abandoned_is_zero_when_every_candidate_was_resolved() -> None:
    counters = _Counters(
        candidates_considered=99,
        skipped_empty_text=0,
        attempted=99,
        succeeded=99,
        failed=0,
    )
    assert counters.abandoned == 0


def test_accounted_for_is_false_when_abandoned_goes_negative() -> None:
    """candidates=5 but attempted=8: more rows were processed than were
    ever selected. abandoned would be -3, which can only mean a row was
    double-counted somewhere -- never a legitimate outcome."""
    counters = _Counters(candidates_considered=5, skipped_empty_text=0, attempted=8)
    assert counters.abandoned == -3
    assert counters.accounted_for() is False


def test_accounted_for_is_false_when_attempted_does_not_split_cleanly() -> None:
    counters = _Counters(
        candidates_considered=5,
        skipped_empty_text=0,
        attempted=5,
        succeeded=3,
        failed=1,
    )
    assert counters.accounted_for() is False


def test_abandoned_unexpectedly_is_false_for_a_run_that_legitimately_aborted() -> None:
    """27 abandoned rows after a QUOTA_EXCEEDED status is exactly what
    an aborted run is supposed to look like -- not a bug to report."""
    counters = _Counters(
        candidates_considered=99,
        skipped_empty_text=0,
        attempted=72,
        succeeded=72,
        failed=0,
    )
    assert counters.abandoned_unexpectedly(aborted=True) is False


def test_abandoned_unexpectedly_is_true_when_nothing_aborted_but_rows_vanished() -> None:
    """The same 27 abandoned rows, but this time nothing in the run
    reported stopping early. That combination has no honest
    explanation and is exactly what this method exists to catch."""
    counters = _Counters(
        candidates_considered=99,
        skipped_empty_text=0,
        attempted=72,
        succeeded=72,
        failed=0,
    )
    assert counters.abandoned_unexpectedly(aborted=False) is True


def test_abandoned_unexpectedly_is_false_when_nothing_was_abandoned() -> None:
    counters = _Counters(
        candidates_considered=99,
        skipped_empty_text=0,
        attempted=99,
        succeeded=99,
        failed=0,
    )
    assert counters.abandoned_unexpectedly(aborted=False) is False


def test_live_run_1_reconstructs_as_a_balanced_funnel() -> None:
    """The exact numbers from the live report's first run: candidates=8,
    api_calls=1, and (after the fix) attempted=0.

    Under the OLD check -- candidates_considered == skipped_empty_text
    + attempted -- these same raw numbers (8, 0, 0) would have failed:
    8 != 0 + 0. That was correct in the narrow sense that the assertion
    fired, but wrong about WHY: nothing was lost, the run simply
    stopped before sending its one and only batch. The fix was not to
    loosen the check but to name the gap -- `abandoned` -- and only
    treat it as a fault when nothing legitimately aborted the run.
    """
    counters = _Counters(
        candidates_considered=8,
        skipped_empty_text=0,
        attempted=0,
        succeeded=0,
        failed=0,
        api_calls=1,
    )
    assert counters.abandoned == 8
    assert counters.accounted_for() is True


def test_live_run_2_reconstructs_as_a_balanced_funnel() -> None:
    """The exact numbers from the live report's second run: 9 successful
    batches of 8 (72 attempted, 72 succeeded) before the 10th batch hit
    the quota. The pre-call increment for that 10th batch is undone on
    the quota path, so attempted lands on exactly 72 -- matching what
    was actually resolved, with the other 27 candidates abandoned."""
    counters = _Counters(
        candidates_considered=99,
        skipped_empty_text=0,
        attempted=72,
        succeeded=72,
        failed=0,
        api_calls=10,
    )
    assert counters.abandoned == 27
    assert counters.accounted_for() is True
