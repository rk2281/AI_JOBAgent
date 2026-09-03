"""Tests for what the workflow stores and what it reports.

No database, no LangGraph import, no event loop -- state.py has none of
those dependencies on purpose, so every value below is passed in
directly.

Two failures are being guarded against specifically. One: an enum or a
dataclass reaching graph state, which would survive every test until a
checkpoint tried to serialise it. Two: a run that computed nothing
reporting as a run that completed, which is section 0's rule applied to
the graph itself.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from app.db.models.embedding import EmbeddingStatus
from app.db.models.ingestion import IngestionStatus
from app.workflows.state import (
    STATUS_COMPLETE,
    STATUS_COMPLETE_NO_QUALIFYING,
    STATUS_COMPLETE_NO_WORK,
    STATUS_DEGRADED,
    STATUS_DRY_RUN,
    STATUS_FAILED,
    STATUS_NO_CANDIDATE_JOBS,
    STATUS_NO_SCORABLE_USERS,
    build_run_summary,
    enrichment_computed,
    embedding_computed,
    ingestion_computed,
    initial_state,
    normalise_embedding_result,
    normalise_enrichment_result,
    normalise_ingestion_result,
    scoring_computed,
    select_graph_status,
)


# --- stand-ins for the two dataclasses the services return ---------------
#
# Shaped to match app.services.job_ingestion.IngestionResult and
# app.services.job_embedding.EmbeddingResult. Built here rather than
# imported so these tests do not need a database URL to import a service
# module; the field names are the contract and are asserted below.


@dataclass
class _FakeIngestionResult:
    run_id: int | None
    status: IngestionStatus
    counters: dict[str, int]
    error: str | None = None


@dataclass
class _FakeEmbeddingCounters:
    candidates_considered: int = 0
    skipped_empty_text: int = 0
    attempted: int = 0
    succeeded: int = 0
    failed: int = 0
    api_calls: int = 0

    @property
    def abandoned(self) -> int:
        return self.candidates_considered - self.skipped_empty_text - self.attempted


@dataclass
class _FakeEmbeddingResult:
    status: EmbeddingStatus
    counters: _FakeEmbeddingCounters = field(default_factory=_FakeEmbeddingCounters)
    remaining_null: int = 0
    total_in_scope: int = 0
    truncated: int = 0
    error_message: str | None = None


def _state_with(**overrides):
    state = initial_state(started_at="2026-09-03T00:00:00+00:00")
    state.update(overrides)
    return state


# --- the one hard rule ---------------------------------------------------


def test_initial_state_is_json_serialisable() -> None:
    state = initial_state(started_at="2026-09-03T00:00:00+00:00")
    assert json.loads(json.dumps(state)) == state


def test_a_fully_populated_state_is_json_serialisable() -> None:
    """The case that matters: after every node has written its result.

    An enum or a dataclass leaking through a normaliser survives every
    other test in this file and fails only when a checkpoint is written.
    """
    state = _state_with(
        targets={"users_with_embedded_cv": 3, "target_user_ids": [2, 3, 10]},
        ingestion=normalise_ingestion_result(
            _FakeIngestionResult(
                run_id=7,
                status=IngestionStatus.COMPLETE,
                counters={"records_fetched": 40, "inserted": 12},
            )
        ),
        embedding=normalise_embedding_result(
            _FakeEmbeddingResult(status=EmbeddingStatus.COMPLETE)
        ),
        enrichment=normalise_enrichment_result({"status": "complete", "call_seconds": [1.5]}),
        scoring={"status": "complete_no_qualifying", "pairs_scored": 294},
        stages_attempted=["discover_jobs"],
        stages_computed=["discover_jobs"],
        finished_at="2026-09-03T00:05:00+00:00",
    )
    assert json.loads(json.dumps(state)) == state


def test_the_summary_is_json_serialisable() -> None:
    summary = build_run_summary(_state_with(finished_at="2026-09-03T00:05:00+00:00"))
    assert json.loads(json.dumps(summary)) == summary


# --- normalisers flatten enums to strings --------------------------------


def test_ingestion_status_enum_becomes_a_string() -> None:
    result = _FakeIngestionResult(
        run_id=1, status=IngestionStatus.NO_RESULTS, counters={"records_fetched": 0}
    )
    normalised = normalise_ingestion_result(result)
    assert isinstance(normalised["status"], str)
    assert normalised["status"] == "no_results"


def test_ingestion_counters_are_flattened_onto_the_result() -> None:
    result = _FakeIngestionResult(
        run_id=1,
        status=IngestionStatus.COMPLETE,
        counters={"records_fetched": 40, "duplicates": 28, "inserted": 12},
    )
    normalised = normalise_ingestion_result(result)
    assert normalised["inserted"] == 12
    assert normalised["duplicates"] == 28


def test_embedding_status_enum_becomes_a_string() -> None:
    result = _FakeEmbeddingResult(status=EmbeddingStatus.QUOTA_EXCEEDED)
    normalised = normalise_embedding_result(result)
    assert isinstance(normalised["status"], str)
    assert normalised["status"] == "quota_exceeded"


def test_embedding_counters_dataclass_is_flattened_not_stored() -> None:
    counters = _FakeEmbeddingCounters(
        candidates_considered=10, skipped_empty_text=1, attempted=4, succeeded=3, failed=1
    )
    normalised = normalise_embedding_result(
        _FakeEmbeddingResult(status=EmbeddingStatus.PARTIAL, counters=counters)
    )
    assert normalised["succeeded"] == 3
    # abandoned is a property, not a field, and names the candidates a
    # quota abort never reached. Dropping it would make them invisible.
    assert normalised["abandoned"] == 5
    assert all(not hasattr(value, "__dataclass_fields__") for value in normalised.values())


def test_enrichment_call_seconds_is_replaced_by_its_total() -> None:
    normalised = normalise_enrichment_result(
        {"status": "complete", "attempted": 3, "call_seconds": [1.5, 2.25, 0.25]}
    )
    assert "call_seconds" not in normalised
    assert normalised["call_seconds_total"] == 4.0


def test_enrichment_normaliser_survives_a_missing_call_seconds() -> None:
    normalised = normalise_enrichment_result({"status": "nothing_to_do"})
    assert normalised["call_seconds_total"] == 0


# --- did this stage compute anything? ------------------------------------


def test_ingestion_that_fetched_nothing_did_not_compute() -> None:
    assert ingestion_computed({"records_fetched": 0}) is False
    assert ingestion_computed({"records_fetched": 1}) is True


def test_embedding_with_nothing_to_do_did_not_compute() -> None:
    assert embedding_computed({"attempted": 0}) is False
    assert embedding_computed({"attempted": 1}) is True


def test_a_dry_run_enrichment_counts_as_computation() -> None:
    """It makes no API call, so attempted stays 0 while it still counts
    every candidate it would have sent."""
    assert enrichment_computed({"attempted": 0, "candidates_considered": 97}) is True


def test_enrichment_stopped_by_quota_on_the_first_job_did_not_compute() -> None:
    """run_enrichment backs the aborted job out of both counters, so
    both are 0 and nothing was computed. This is the case that must not
    read as a quiet success."""
    assert enrichment_computed({"attempted": 0, "candidates_considered": 0}) is False


def test_scoring_computes_only_when_pairs_were_scored() -> None:
    assert scoring_computed({"pairs_scored": 0}) is False
    assert scoring_computed({"pairs_scored": 294}) is True


# --- status precedence ---------------------------------------------------


def _status(**overrides) -> str:
    kwargs = {
        "errors": [],
        "terminal_reason": None,
        "degraded": False,
        "computation_performed": True,
        "writes_prevented": False,
        "notify_eligible": 0,
    }
    kwargs.update(overrides)
    return select_graph_status(**kwargs)


def test_an_error_outranks_everything() -> None:
    assert _status(errors=["boom"], degraded=True, notify_eligible=5) == STATUS_FAILED


def test_stopping_at_target_resolution_outranks_degradation() -> None:
    assert (
        _status(terminal_reason=STATUS_NO_SCORABLE_USERS, degraded=True)
        == STATUS_NO_SCORABLE_USERS
    )


def test_no_candidate_jobs_is_its_own_status() -> None:
    assert _status(terminal_reason=STATUS_NO_CANDIDATE_JOBS) == STATUS_NO_CANDIDATE_JOBS


def test_quota_exhaustion_is_degraded_not_no_work() -> None:
    """The whole point of the decomposition: a quota-stopped run and an
    idle night both produce small numbers, and only one is a problem."""
    assert _status(degraded=True, computation_performed=False) == STATUS_DEGRADED


def test_computing_nothing_is_not_complete() -> None:
    assert _status(computation_performed=False) == STATUS_COMPLETE_NO_WORK
    assert _status(computation_performed=False) != STATUS_COMPLETE


def test_a_dry_run_that_computed_nothing_reports_no_work_not_dry_run() -> None:
    assert (
        _status(computation_performed=False, writes_prevented=True)
        == STATUS_COMPLETE_NO_WORK
    )


def test_a_dry_run_that_computed_reports_dry_run() -> None:
    assert _status(computation_performed=True, writes_prevented=True) == STATUS_DRY_RUN


def test_nothing_cleared_the_gate_is_the_healthy_quiet_day() -> None:
    assert _status(notify_eligible=0) == STATUS_COMPLETE_NO_QUALIFYING


def test_something_cleared_the_gate() -> None:
    assert _status(notify_eligible=1) == STATUS_COMPLETE


def test_notify_eligible_of_exactly_one_is_enough() -> None:
    """The boundary. is_notify_eligible uses >= throughout and one
    eligible pair is a notification, not a rounding error."""
    assert _status(notify_eligible=1) == STATUS_COMPLETE
    assert _status(notify_eligible=0) == STATUS_COMPLETE_NO_QUALIFYING


# --- the summary is pure -------------------------------------------------


def test_two_calls_on_the_same_state_are_equal() -> None:
    state = _state_with(finished_at="2026-09-03T00:05:00+00:00")
    assert build_run_summary(state) == build_run_summary(state)


def test_the_summary_never_reads_a_clock() -> None:
    """finished_at comes from state, not from datetime.now(). A summary
    that consults the time cannot be compared against itself."""
    state = _state_with(finished_at="1999-01-01T00:00:00+00:00")
    assert build_run_summary(state)["finished_at"] == "1999-01-01T00:00:00+00:00"


def test_the_summary_does_not_mutate_the_state_it_reads() -> None:
    state = _state_with(stages_attempted=["discover_jobs"], finished_at="x")
    before = json.dumps(state)
    build_run_summary(state)
    assert json.dumps(state) == before


# --- zero work stays visible ---------------------------------------------


def test_a_run_that_skipped_everything_reports_no_work() -> None:
    summary = build_run_summary(
        _state_with(
            stages_skipped=[
                "discover_jobs: skip_ingestion",
                "embed_jobs: skip_embedding",
                "enrich_jobs: skip_enrichment",
            ],
            finished_at="2026-09-03T00:00:01+00:00",
        )
    )
    assert summary["computation_performed"] is False
    assert summary["persistence_performed"] is False
    assert summary["status"] == STATUS_COMPLETE_NO_WORK
    assert summary["status"] != STATUS_COMPLETE


def test_a_dry_run_that_scored_everything_is_not_reported_as_idle() -> None:
    """The case one did_work boolean got wrong: 294 pairs computed and
    nothing written."""
    summary = build_run_summary(
        _state_with(
            dry_run=True,
            scoring={"status": "dry_run", "pairs_scored": 294, "users_scored": 3},
            stages_attempted=["score_and_rank"],
            stages_computed=["score_and_rank"],
            stages_skipped=["discover_jobs: dry_run", "embed_jobs: dry_run"],
            finished_at="2026-09-03T00:05:00+00:00",
        )
    )
    assert summary["computation_performed"] is True
    assert summary["persistence_performed"] is False
    assert summary["writes_prevented"] is True
    assert summary["status"] == STATUS_DRY_RUN
    assert summary["pairs_scored"] == 294


def test_a_quota_stopped_enrichment_is_degraded_in_the_summary() -> None:
    summary = build_run_summary(
        _state_with(
            enrichment={"status": "quota_exceeded", "attempted": 0, "candidates_considered": 0},
            scoring={"status": "complete_no_qualifying", "pairs_scored": 294},
            stages_attempted=["enrich_jobs", "score_and_rank"],
            stages_computed=["score_and_rank"],
            stages_skipped=["enrich_jobs: quota_exceeded"],
            finished_at="2026-09-03T00:05:00+00:00",
        )
    )
    assert summary["status"] == STATUS_DEGRADED
    assert "enrich_jobs: quota_exceeded" in summary["stages_skipped"]


def test_a_skip_reason_is_never_merged_into_stages_attempted() -> None:
    summary = build_run_summary(
        _state_with(
            stages_attempted=["score_and_rank"],
            stages_skipped=["discover_jobs: dry_run"],
            finished_at="x",
        )
    )
    assert summary["stages_attempted"] == ["score_and_rank"]
    assert summary["stages_skipped"] == ["discover_jobs: dry_run"]
