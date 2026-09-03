"""Each node, with its service stubbed. No network, no database, no quota.

pytest-asyncio is not installed and must not be. Async tests here are
plain synchronous functions driving a coroutine with asyncio.run(), the
same pattern tests/test_job_ingestion.py uses.

Every service is monkeypatched at the name app.workflows.nodes imported
it under. Where a stage is supposed to be SKIPPED, the stub raises --
so a node that quietly called it anyway fails loudly instead of passing
because the numbers happened to look reasonable.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

import pytest

from app.db.models.embedding import EmbeddingStatus
from app.db.models.ingestion import IngestionStatus
from app.db.models.scoring import ScoringStatus
from app.workflows import nodes


def _run(coro):
    return asyncio.run(coro)


class _Boom(AssertionError):
    """Raised by a stub that should never have been called."""


def _never(*args, **kwargs):
    raise _Boom("this service must not be called")


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


class _FakeAdzunaClient:
    """Stands in for the async context manager the node opens."""

    def __init__(self, *, credentials: bool = True) -> None:
        self._credentials = credentials
        self.entered = False

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, *exc_info):
        return None

    def credentials_present(self) -> bool:
        return self._credentials


def _assert_json_safe(update: dict) -> None:
    assert json.loads(json.dumps(update)) == update


# --- resolve_targets -----------------------------------------------------


def test_resolve_targets_stops_the_run_when_nobody_is_scorable(monkeypatch) -> None:
    async def fake(*, user_id=None):
        return {
            "requested_user_id": user_id,
            "users_considered": 3,
            "users_with_profile": 3,
            "users_with_embedded_cv": 0,
            "target_user_ids": [],
        }

    monkeypatch.setattr(nodes, "resolve_scoring_targets", fake)
    update = _run(nodes.resolve_targets({"user_id": None}))

    assert update["terminal_reason"] == "no_scorable_users"
    assert update["stages_attempted"] == ["resolve_targets"]
    _assert_json_safe(update)


def test_resolve_targets_continues_with_one_scorable_user(monkeypatch) -> None:
    async def fake(*, user_id=None):
        return {
            "requested_user_id": user_id,
            "users_considered": 1,
            "users_with_profile": 1,
            "users_with_embedded_cv": 1,
            "target_user_ids": [2],
        }

    monkeypatch.setattr(nodes, "resolve_scoring_targets", fake)
    update = _run(nodes.resolve_targets({"user_id": 2}))

    assert "terminal_reason" not in update
    assert update["targets"]["target_user_ids"] == [2]


# --- discover_jobs -------------------------------------------------------


def test_dry_run_skips_ingestion_with_a_reason(monkeypatch) -> None:
    """run_ingestion has no dry_run parameter; calling it would hit
    Adzuna and insert rows."""
    monkeypatch.setattr(nodes, "run_ingestion", _never)
    monkeypatch.setattr(nodes, "AdzunaClient", _never)

    update = _run(nodes.discover_jobs({"dry_run": True}))

    assert update == {"stages_skipped": ["discover_jobs: dry_run"]}


def test_skip_ingestion_flag_skips_with_its_own_reason(monkeypatch) -> None:
    monkeypatch.setattr(nodes, "run_ingestion", _never)
    monkeypatch.setattr(nodes, "AdzunaClient", _never)

    update = _run(nodes.discover_jobs({"skip_ingestion": True}))

    assert update == {"stages_skipped": ["discover_jobs: skip_ingestion"]}


def test_missing_adzuna_credentials_records_a_skip_rather_than_raising(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        nodes, "AdzunaClient", lambda: _FakeAdzunaClient(credentials=False)
    )
    monkeypatch.setattr(nodes, "run_ingestion", _never)

    update = _run(nodes.discover_jobs({}))

    assert update == {"stages_skipped": ["discover_jobs: credentials_missing"]}


def test_a_successful_ingestion_is_recorded_as_computed_and_persisted(
    monkeypatch,
) -> None:
    async def fake(source, **kwargs):
        return _FakeIngestionResult(
            run_id=7,
            status=IngestionStatus.COMPLETE,
            counters={"records_fetched": 40, "duplicates": 28, "inserted": 12},
        )

    monkeypatch.setattr(nodes, "AdzunaClient", _FakeAdzunaClient)
    monkeypatch.setattr(nodes, "run_ingestion", fake)

    update = _run(nodes.discover_jobs({}))

    assert update["ingestion"]["status"] == "complete"
    assert update["ingestion"]["inserted"] == 12
    assert update["stages_computed"] == ["discover_jobs"]
    assert update["stages_persisted"] == ["discover_jobs"]
    _assert_json_safe(update)


def test_an_ingestion_that_fetched_nothing_is_not_counted_as_computation(
    monkeypatch,
) -> None:
    async def fake(source, **kwargs):
        return _FakeIngestionResult(
            run_id=8, status=IngestionStatus.NO_RESULTS, counters={"records_fetched": 0}
        )

    monkeypatch.setattr(nodes, "AdzunaClient", _FakeAdzunaClient)
    monkeypatch.setattr(nodes, "run_ingestion", fake)

    update = _run(nodes.discover_jobs({}))

    assert "stages_computed" not in update
    assert update["stages_attempted"] == ["discover_jobs"]


def test_an_ingestion_failure_does_not_stop_the_graph(monkeypatch) -> None:
    """Jobs already in the database are still scorable. A source outage
    is not a reason to skip scoring the rows that are already there."""

    async def fake(source, **kwargs):
        return _FakeIngestionResult(
            run_id=9,
            status=IngestionStatus.SOURCE_ERROR,
            counters={"records_fetched": 0},
            error="HTTP 503 from the provider",
        )

    monkeypatch.setattr(nodes, "AdzunaClient", _FakeAdzunaClient)
    monkeypatch.setattr(nodes, "run_ingestion", fake)

    update = _run(nodes.discover_jobs({}))

    assert update["ingestion"]["status"] == "source_error"
    assert "terminal_reason" not in update
    assert "errors" not in update


# --- embed_jobs ----------------------------------------------------------


def test_dry_run_skips_embedding_with_a_reason(monkeypatch) -> None:
    monkeypatch.setattr(nodes, "run_job_embedding", _never)
    update = _run(nodes.embed_jobs({"dry_run": True}))
    assert update == {"stages_skipped": ["embed_jobs: dry_run"]}


def test_skip_embedding_flag_skips_with_its_own_reason(monkeypatch) -> None:
    monkeypatch.setattr(nodes, "run_job_embedding", _never)
    update = _run(nodes.embed_jobs({"skip_embedding": True}))
    assert update == {"stages_skipped": ["embed_jobs: skip_embedding"]}


def test_embedding_flattens_its_nested_counters(monkeypatch) -> None:
    async def fake(*args, **kwargs):
        return _FakeEmbeddingResult(
            status=EmbeddingStatus.COMPLETE,
            counters=_FakeEmbeddingCounters(
                candidates_considered=12, attempted=12, succeeded=12, api_calls=12
            ),
            remaining_null=0,
        )

    monkeypatch.setattr(nodes, "run_job_embedding", fake)
    update = _run(nodes.embed_jobs({}))

    assert update["embedding"]["succeeded"] == 12
    assert update["stages_computed"] == ["embed_jobs"]
    _assert_json_safe(update)


def test_embedding_with_nothing_to_do_is_not_computation(monkeypatch) -> None:
    async def fake(*args, **kwargs):
        return _FakeEmbeddingResult(status=EmbeddingStatus.NOTHING_TO_DO)

    monkeypatch.setattr(nodes, "run_job_embedding", fake)
    update = _run(nodes.embed_jobs({}))

    assert update["embedding"]["status"] == "nothing_to_do"
    assert "stages_computed" not in update


# --- enrich_jobs ---------------------------------------------------------


def test_skip_enrichment_flag_skips_with_its_own_reason(monkeypatch) -> None:
    monkeypatch.setattr(nodes, "run_enrichment", _never)
    update = _run(nodes.enrich_jobs({"skip_enrichment": True}))
    assert update == {"stages_skipped": ["enrich_jobs: skip_enrichment"]}


def test_dry_run_calls_enrichment_with_dry_run_true(monkeypatch) -> None:
    """Unlike ingestion and embedding, run_enrichment DOES take
    dry_run, so a dry run calls it rather than skipping it."""
    seen = {}

    async def fake(*, limit=None, dry_run=False):
        seen["limit"] = limit
        seen["dry_run"] = dry_run
        return {"status": "complete", "attempted": 0, "candidates_considered": 97}

    monkeypatch.setattr(nodes, "run_enrichment", fake)
    update = _run(nodes.enrich_jobs({"dry_run": True, "enrichment_limit": 5}))

    assert seen == {"limit": 5, "dry_run": True}
    assert update["stages_computed"] == ["enrich_jobs"]
    assert "stages_persisted" not in update


def test_quota_exhaustion_is_recorded_as_a_skip_not_a_clean_pass(monkeypatch) -> None:
    """run_enrichment does not raise on a quota error -- it stops and
    returns normally with small numbers. Without reading the status,
    that reads as a quiet, healthy night."""

    async def fake(*, limit=None, dry_run=False):
        return {
            "status": "quota_exceeded",
            "attempted": 0,
            "candidates_considered": 0,
            "succeeded": 0,
            "error_message": "429 RESOURCE_EXHAUSTED",
        }

    monkeypatch.setattr(nodes, "run_enrichment", fake)
    update = _run(nodes.enrich_jobs({}))

    assert update["stages_skipped"] == ["enrich_jobs: quota_exceeded"]
    assert "stages_computed" not in update
    assert update["enrichment"]["status"] == "quota_exceeded"


def test_enrichment_drops_call_seconds_from_state(monkeypatch) -> None:
    async def fake(*, limit=None, dry_run=False):
        return {"status": "complete", "attempted": 2, "call_seconds": [1.0, 2.0]}

    monkeypatch.setattr(nodes, "run_enrichment", fake)
    update = _run(nodes.enrich_jobs({}))

    assert "call_seconds" not in update["enrichment"]
    assert update["enrichment"]["call_seconds_total"] == 3.0


# --- score_and_rank ------------------------------------------------------


def test_dry_run_calls_scoring_with_dry_run_true(monkeypatch) -> None:
    seen = {}

    async def fake(*, user_id=None, dry_run=False):
        seen["user_id"] = user_id
        seen["dry_run"] = dry_run
        return {"status": "dry_run", "pairs_scored": 294, "notify_eligible": 0}

    monkeypatch.setattr(nodes, "run_scoring", fake)
    update = _run(nodes.score_and_rank({"dry_run": True, "user_id": 2}))

    assert seen == {"user_id": 2, "dry_run": True}
    assert update["stages_computed"] == ["score_and_rank"]
    assert "stages_persisted" not in update


def test_a_real_scoring_run_is_recorded_as_persisted(monkeypatch) -> None:
    async def fake(*, user_id=None, dry_run=False):
        return {"status": "complete_no_qualifying", "pairs_scored": 294, "run_id": 3}

    monkeypatch.setattr(nodes, "run_scoring", fake)
    update = _run(nodes.score_and_rank({}))

    assert update["stages_persisted"] == ["score_and_rank"]
    assert "terminal_reason" not in update


@pytest.mark.parametrize(
    "status", [ScoringStatus.NO_CANDIDATE_JOBS.value, ScoringStatus.NO_SCORABLE_USERS.value]
)
def test_scoring_statuses_that_end_the_run_set_a_terminal_reason(
    monkeypatch, status: str
) -> None:
    async def fake(*, user_id=None, dry_run=False):
        return {"status": status, "pairs_scored": 0}

    monkeypatch.setattr(nodes, "run_scoring", fake)
    update = _run(nodes.score_and_rank({}))

    assert update["terminal_reason"] == status
    assert "stages_computed" not in update


# --- decide_notification -------------------------------------------------


def test_no_eligible_pair_takes_the_quiet_branch() -> None:
    update = _run(
        nodes.decide_notification({"scoring": {"notify_eligible": 0, "status": "complete"}})
    )
    assert update["notify_branch"] == "no_qualifying"
    assert update["notify_eligible"] == 0


def test_one_eligible_pair_takes_the_notify_branch() -> None:
    """The branch Day 11 sends real messages from, exercised at its
    boundary before anything is attached to it."""
    update = _run(nodes.decide_notification({"scoring": {"notify_eligible": 1}}))
    assert update["notify_branch"] == "notify"


def test_decide_notification_applies_no_threshold_of_its_own() -> None:
    """It reads the count run_scoring already produced with the three
    inclusive gates. A second copy of that rule would be a second thing
    to keep in step."""
    update = _run(
        nodes.decide_notification(
            {"scoring": {"notify_eligible": 3, "final_score": 0.0, "weight_covered": 0.0}}
        )
    )
    assert update["notify_eligible"] == 3
    assert update["notify_branch"] == "notify"


def test_a_missing_scoring_block_is_not_a_notification() -> None:
    update = _run(nodes.decide_notification({}))
    assert update["notify_branch"] == "no_qualifying"


# --- finalise ------------------------------------------------------------


def test_finalise_stamps_the_end_time_as_a_string() -> None:
    update = _run(nodes.finalise({}))
    assert isinstance(update["finished_at"], str)
    assert update["stages_attempted"] == ["finalise"]
    _assert_json_safe(update)


# --- every node returns JSON-safe primitives -----------------------------


def test_no_node_returns_a_reason_string_without_a_colon() -> None:
    """A skip entry is "name: reason". A bare name would be
    indistinguishable from a stage that ran and found nothing."""
    updates = [
        _run(nodes.discover_jobs({"dry_run": True})),
        _run(nodes.embed_jobs({"dry_run": True})),
        _run(nodes.enrich_jobs({"skip_enrichment": True})),
    ]
    for update in updates:
        for entry in update["stages_skipped"]:
            assert ": " in entry
