"""What travels between nodes, and what a finished run reports.

THE ONE HARD RULE

Everything stored at a node boundary is JSON-serialisable: str, int,
float, bool, None, list, dict. No dataclass, no enum, no ORM instance,
no session, no client, no callable. Two reasons, and the second is the
one that bites.

First, LangGraph checkpoints state. A state holding 99 job descriptions
and 768-dimension vectors is a serialisation problem nobody asked for.

Second, every service here already owns its own transactions and
commits per unit. Passing rows between nodes would mean holding
SQLAlchemy instances across session boundaries -- detached instances,
which is a whole class of bug this codebase currently does not have and
should not acquire in order to draw a graph.

So nodes talk to each other in COUNTERS, and to the database in ROWS.
run_scoring writes 98 recommendation rows to Postgres; if those rows
also travelled through graph state there would be two copies of the
ranking and no test that they agree. The database is the answer.

WHY did_work DOES NOT EXIST

An earlier draft had a single did_work boolean. It was wrong, and
wrong in the exact shape section 0 warns about: a dry run computes
every signal on every pair and deliberately writes nothing, while a
quota-stopped run writes nothing because it did nothing. One boolean
cannot tell those apart, so it would have read "no work" for a run that
scored 294 pairs, or "work" for a run that made one failed API call.

Four separate lists and two separate booleans instead:

  stages_attempted  entered its work path and called its service
  stages_skipped    "name: reason", never merged with the above
  stages_computed   the service actually computed over domain data
  stages_persisted  durable rows were written

computation_performed and persistence_performed are derived from the
last two. A dry run that scores everything reports computation
performed, persistence not, writes_prevented true, and two explicit
skip reasons. Nothing about that reads as an idle run, and nothing
about it reads as a persisted one.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

# --- graph statuses ------------------------------------------------------
#
# Strings rather than an enum, because these values live IN the state and
# the state is JSON. The precedence between them is in
# select_graph_status(), and it is load-bearing.

STATUS_FAILED = "failed"
STATUS_NO_SCORABLE_USERS = "no_scorable_users"
STATUS_NO_CANDIDATE_JOBS = "no_candidate_jobs"
STATUS_DEGRADED = "degraded"
STATUS_COMPLETE_NO_WORK = "complete_no_work"
STATUS_DRY_RUN = "dry_run"
STATUS_COMPLETE_NO_QUALIFYING = "complete_no_qualifying"
STATUS_COMPLETE = "complete"

# A service status meaning "this stage did not do what it was asked".
# quota_exceeded is the important one: run_enrichment STOPS on a quota
# error and returns normally, so a quota-exhausted day otherwise looks
# like a successful enrichment that happened to return small numbers.
# That is the complete_no_qualifying failure shape repeating one layer
# up, and it is why the graph reads statuses rather than counters to
# decide whether a run went well.
DEGRADED_SERVICE_STATUSES = frozenset(
    {
        "quota_exceeded",
        "source_error",
        "provider_error",
        "all_failed",
        "all_abstained",
        "degenerate",
        "partial",
        "failed",
    }
)


class AgentState(TypedDict, total=False):
    """The graph's state. A TypedDict, not a dataclass.

    LangGraph state must be a mapping, and a dataclass would have to be
    encoded to survive a checkpoint -- which is the same rule as the
    module docstring, applied to the container itself.

    The four list fields carry operator.add reducers so a node's
    partial return APPENDS. Without a reducer LangGraph replaces the
    value, and stages_skipped would silently keep only the last node's
    entry: a skip-recording mechanism that loses skips.
    """

    # --- inputs, set once at entry, never mutated by a node ---
    user_id: int | None
    dry_run: bool
    skip_ingestion: bool
    skip_embedding: bool
    skip_enrichment: bool
    ingestion_keywords: list[str] | None
    ingestion_locations: list[str] | None
    ingestion_max_pages: int | None
    enrichment_limit: int | None
    started_at: str

    # --- per-stage results: flat dicts of primitives, status a str ---
    targets: dict[str, Any] | None
    ingestion: dict[str, Any] | None
    embedding: dict[str, Any] | None
    enrichment: dict[str, Any] | None
    scoring: dict[str, Any] | None

    # --- bookkeeping ---
    stages_attempted: Annotated[list[str], operator.add]
    stages_skipped: Annotated[list[str], operator.add]
    stages_computed: Annotated[list[str], operator.add]
    stages_persisted: Annotated[list[str], operator.add]
    errors: Annotated[list[str], operator.add]
    notify_branch: str | None
    notify_eligible: int | None
    terminal_reason: str | None
    finished_at: str | None


def initial_state(
    *,
    user_id: int | None = None,
    dry_run: bool = False,
    skip_ingestion: bool = False,
    skip_embedding: bool = False,
    skip_enrichment: bool = False,
    ingestion_keywords: list[str] | None = None,
    ingestion_locations: list[str] | None = None,
    ingestion_max_pages: int | None = None,
    enrichment_limit: int | None = None,
    started_at: str,
) -> AgentState:
    """Every key present from the start, so no node reads a missing one.

    started_at is passed in rather than read from a clock here, for the
    same reason build_run_summary does not read one: a function that
    consults the time cannot be compared against itself.
    """
    return AgentState(
        user_id=user_id,
        dry_run=dry_run,
        skip_ingestion=skip_ingestion,
        skip_embedding=skip_embedding,
        skip_enrichment=skip_enrichment,
        ingestion_keywords=ingestion_keywords,
        ingestion_locations=ingestion_locations,
        ingestion_max_pages=ingestion_max_pages,
        enrichment_limit=enrichment_limit,
        started_at=started_at,
        targets=None,
        ingestion=None,
        embedding=None,
        enrichment=None,
        scoring=None,
        stages_attempted=[],
        stages_skipped=[],
        stages_computed=[],
        stages_persisted=[],
        errors=[],
        notify_branch=None,
        notify_eligible=None,
        terminal_reason=None,
        finished_at=None,
    )


# --- normalisers ---------------------------------------------------------
#
# The four services return three different shapes: run_ingestion an
# IngestionResult dataclass whose status is an enum, run_job_embedding an
# EmbeddingResult dataclass whose status is an enum and whose counters are
# a nested dataclass, run_enrichment and run_scoring plain dicts with str
# statuses. These three functions are where that becomes one shape.
#
# They take the objects and return dicts; they never store the objects.


def normalise_ingestion_result(result: Any) -> dict[str, Any]:
    """IngestionResult -> flat dict. counters is already dict[str, int]."""
    normalised: dict[str, Any] = {
        "status": result.status.value,
        "run_id": result.run_id,
        "error": result.error,
    }
    normalised.update(dict(result.counters))
    return normalised


def normalise_embedding_result(result: Any) -> dict[str, Any]:
    """EmbeddingResult -> flat dict.

    counters is a dataclass, not a dict, so its fields are copied out
    by name. `abandoned` is a property rather than a field and is
    included deliberately: it names the candidates a quota abort left
    unprocessed, which is precisely the number that would otherwise be
    invisible.
    """
    counters = result.counters
    return {
        "status": result.status.value,
        "remaining_null": result.remaining_null,
        "total_in_scope": result.total_in_scope,
        "truncated": result.truncated,
        "error_message": result.error_message,
        "candidates_considered": counters.candidates_considered,
        "skipped_empty_text": counters.skipped_empty_text,
        "attempted": counters.attempted,
        "succeeded": counters.succeeded,
        "failed": counters.failed,
        "api_calls": counters.api_calls,
        "abandoned": counters.abandoned,
    }


def normalise_enrichment_result(result: dict[str, Any]) -> dict[str, Any]:
    """run_enrichment's dict, minus call_seconds.

    call_seconds is a per-call float list that reaches ~97 entries on a
    full pass. It would be serialised into every checkpoint and informs
    no decision the graph makes, so it is replaced by its total. The
    per-call detail still exists in the enrichment run row.
    """
    normalised = {key: value for key, value in result.items() if key != "call_seconds"}
    call_seconds = result.get("call_seconds") or []
    normalised["call_seconds_total"] = round(sum(call_seconds), 3)
    return normalised


# --- did this stage actually compute anything? ---------------------------
#
# Each reads the counter that means "work happened" for that service.
# Deliberately not "status == complete": a run can complete having found
# nothing to do, and that is the case these exist to separate.


def ingestion_computed(result: dict[str, Any]) -> bool:
    return int(result.get("records_fetched") or 0) > 0


def embedding_computed(result: dict[str, Any]) -> bool:
    return int(result.get("attempted") or 0) > 0


def enrichment_computed(result: dict[str, Any]) -> bool:
    """attempted under a real run, candidates_considered under a dry run.

    A dry run makes no API call, so attempted stays 0 while
    candidates_considered counts what it would have sent. Both are
    computation over domain data. If a quota error lands on the very
    first job, run_enrichment backs that job out of both counters and
    this correctly reports False -- nothing was computed.
    """
    attempted = int(result.get("attempted") or 0)
    considered = int(result.get("candidates_considered") or 0)
    return max(attempted, considered) > 0


def scoring_computed(result: dict[str, Any]) -> bool:
    return int(result.get("pairs_scored") or 0) > 0


def is_degraded_status(status: str | None) -> bool:
    return status in DEGRADED_SERVICE_STATUSES


def select_graph_status(
    *,
    errors: list[str],
    terminal_reason: str | None,
    degraded: bool,
    computation_performed: bool,
    writes_prevented: bool,
    notify_eligible: int | None,
) -> str:
    """The terminal status of one graph run. ORDER IS LOAD-BEARING.

    Read it downward; each line is a reason the lines below it must not
    be reached.

    An unhandled error outranks everything, because a run that broke
    cannot describe its own outcome.

    A terminal_reason -- stopped at target resolution, or scoring found
    no candidate jobs -- outranks degradation, because the run stopped
    before the degraded stage could matter.

    DEGRADED outranks COMPLETE_NO_WORK. A quota-stopped enrichment and
    an idle night both produce small numbers; only the first is a
    problem, and reporting it as "nothing to do" is exactly the
    complete_no_qualifying mistake one layer up.

    COMPLETE_NO_WORK outranks DRY_RUN, because "computed nothing" is
    the more important fact about a run than "was not going to write
    anyway". writes_prevented stays in the summary either way, so the
    dry-run-ness is never lost -- it is just not the headline.

    Below that, the two healthy endings, which differ only in whether
    anything cleared the notification gate.
    """
    if errors:
        return STATUS_FAILED
    if terminal_reason == STATUS_NO_SCORABLE_USERS:
        return STATUS_NO_SCORABLE_USERS
    if terminal_reason == STATUS_NO_CANDIDATE_JOBS:
        return STATUS_NO_CANDIDATE_JOBS
    if degraded:
        return STATUS_DEGRADED
    if not computation_performed:
        return STATUS_COMPLETE_NO_WORK
    if writes_prevented:
        return STATUS_DRY_RUN
    if notify_eligible:
        return STATUS_COMPLETE
    return STATUS_COMPLETE_NO_QUALIFYING


def build_run_summary(state: AgentState) -> dict[str, Any]:
    """What one graph run reports, as a pure function of its state.

    Pure means two things concretely, and both are tested: it reads no
    clock and it touches no database. finished_at is written into state
    by the finalise node BEFORE this is called, so two calls on the same
    state return equal dicts -- which is what makes it possible to test
    a summary at all, and what makes Day 10's agent_runs migration a
    matter of persisting a dict that already exists rather than
    designing a schema for a graph whose node set is still moving.

    There is no agent_runs table on Day 9. Every wrapped service already
    wrote its own run row, so nothing about the WORK is unrecorded; what
    is unrecorded is the graph's own decisions, and on Day 9 those are
    read by a person watching a script print them.
    """
    ingestion = state.get("ingestion") or {}
    embedding = state.get("embedding") or {}
    enrichment = state.get("enrichment") or {}
    scoring = state.get("scoring") or {}
    targets = state.get("targets") or {}

    stages_computed = list(state.get("stages_computed") or [])
    stages_persisted = list(state.get("stages_persisted") or [])

    degraded = any(
        is_degraded_status(result.get("status"))
        for result in (ingestion, embedding, enrichment, scoring)
    )

    status = select_graph_status(
        errors=list(state.get("errors") or []),
        terminal_reason=state.get("terminal_reason"),
        degraded=degraded,
        computation_performed=bool(stages_computed),
        writes_prevented=bool(state.get("dry_run")),
        notify_eligible=state.get("notify_eligible"),
    )

    return {
        "status": status,
        "dry_run": bool(state.get("dry_run")),
        "user_id": state.get("user_id"),
        "started_at": state.get("started_at"),
        "finished_at": state.get("finished_at"),
        "writes_prevented": bool(state.get("dry_run")),
        "computation_performed": bool(stages_computed),
        "persistence_performed": bool(stages_persisted),
        "stages_attempted": list(state.get("stages_attempted") or []),
        "stages_skipped": list(state.get("stages_skipped") or []),
        "stages_computed": stages_computed,
        "stages_persisted": stages_persisted,
        "errors": list(state.get("errors") or []),
        "terminal_reason": state.get("terminal_reason"),
        "notify_branch": state.get("notify_branch"),
        "notify_eligible": state.get("notify_eligible"),
        "users_considered": targets.get("users_considered"),
        "users_with_profile": targets.get("users_with_profile"),
        "users_with_embedded_cv": targets.get("users_with_embedded_cv"),
        "ingestion_status": ingestion.get("status"),
        "ingestion_run_id": ingestion.get("run_id"),
        "jobs_inserted": ingestion.get("inserted"),
        "embedding_status": embedding.get("status"),
        "jobs_embedded": embedding.get("succeeded"),
        "embeddings_remaining_null": embedding.get("remaining_null"),
        "enrichment_status": enrichment.get("status"),
        "jobs_enriched": enrichment.get("succeeded"),
        "enrichment_remaining_null": enrichment.get("remaining_null"),
        "scoring_status": scoring.get("status"),
        "scoring_run_id": scoring.get("run_id"),
        # The skip total and the three causes behind it. run_agent.py is
        # the path that runs unattended with nobody watching, so a
        # scheduled run that silently stopped scoring somebody would
        # otherwise emit a summary indistinguishable from a healthy
        # quiet day, in the only artifact that run produces.
        #
        # .get() with no default, like every counter around it: absent
        # is None and NOT zero. A scoring stage that never ran has no
        # opinion about how many users were skipped, and defaulting it
        # to 0 would state one -- the same mistake as defaulting an
        # abstained signal column to 0.0.
        "users_skipped_no_cv": scoring.get("users_skipped_no_cv"),
        "users_skipped_no_profile": scoring.get("users_skipped_no_profile"),
        "users_skipped_no_active_cv": scoring.get("users_skipped_no_active_cv"),
        "users_skipped_cv_not_embedded": scoring.get("users_skipped_cv_not_embedded"),
        "users_scored": scoring.get("users_scored"),
        "jobs_scored": scoring.get("jobs_scored"),
        "pairs_scored": scoring.get("pairs_scored"),
        "jobs_skipped_no_embedding": scoring.get("jobs_skipped_no_embedding"),
    }
