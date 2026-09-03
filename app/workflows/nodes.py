"""The seven nodes. Each one calls exactly one thing and records what it did.

A node is a unit of ORCHESTRATION, not a unit of computation. Nothing
here scores, matches, ranks, validates or deduplicates -- every one of
those already lives inside a service that owns its transactions and
asserts its own funnel. run_scoring's `pairs_scored == jobs_scored x
users_scored` is the only assertion in this codebase that observes the
loop rather than the plan; splitting scoring across nodes would leave it
nothing to assert over.

WHY EVERY NODE ALWAYS RUNS

Skipping is decided INSIDE a node, never by an edge that routes around
it. An edge that bypasses a node leaves nobody to write the skip entry,
and a stage that vanishes without a number is the graph-level version of
a silently excluded row. It also keeps the edge count at three, and a
typo'd edge target is the failure LangGraph reports only at run time, on
the branch that is never taken.

So a node returns either a result or a skip record, and both are
visible in the summary.

WHAT A NODE MAY NOT DO

No SQL and no repository import: resolve_targets calls a service, which
is the only reason app/workflows/ needs to know anything about users at
all. No session, client, engine or ORM instance may appear in a returned
dict -- see state.py.

And no node builds an error string out of a raw exception from
app/integrations/. Adzuna's app_id and app_key are QUERY PARAMETERS, so
the URL is a credential and any exception whose text contains it is a
leak. AdzunaClient already handles this: it passes provider errors
through describe_http_error() and raises `from None` specifically so a
chained traceback cannot print the original URL. The nodes below read
the already-redacted `result.error`; they never format an exception of
their own.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.integrations.adzuna import AdzunaClient
from app.services.job_embedding import run_job_embedding
from app.services.job_enrichment import run_enrichment
from app.services.job_ingestion import run_ingestion
from app.services.job_scoring import resolve_scoring_targets, run_scoring
from app.workflows.routing import SCORING_TERMINAL_STATUSES, route_notification
from app.workflows.state import (
    STATUS_NO_SCORABLE_USERS,
    embedding_computed,
    enrichment_computed,
    ingestion_computed,
    normalise_embedding_result,
    normalise_enrichment_result,
    normalise_ingestion_result,
    scoring_computed,
)


def _skip(node: str, reason: str) -> dict[str, Any]:
    """A skipped stage, recorded with WHY.

    "enrich_jobs" alone would be indistinguishable from a stage that ran
    and found nothing. If the scheduler skips enrichment on eleven
    consecutive nights on Day 10, that must read as eleven skips with a
    reason, not be inferred from counters that never moved.
    """
    return {"stages_skipped": [f"{node}: {reason}"]}


async def resolve_targets(state: dict[str, Any]) -> dict[str, Any]:
    """Is there anybody worth running for?

    Asked before ingestion so a run that would score nobody does not
    first spend an Adzuna pass and a day of Gemini quota finding that
    out. run_scoring answers the same question, but only after doing
    all of the work.

    Calls a service, not a repository, and receives back nothing but
    ints -- so no ORM instance and no session crosses into graph state.
    """
    targets = await resolve_scoring_targets(user_id=state.get("user_id"))

    update: dict[str, Any] = {
        "targets": targets,
        "stages_attempted": ["resolve_targets"],
    }
    if int(targets.get("users_with_embedded_cv") or 0) < 1:
        update["terminal_reason"] = STATUS_NO_SCORABLE_USERS
    return update


async def discover_jobs(state: dict[str, Any]) -> dict[str, Any]:
    """Ingest, unless there is a reason not to -- and say which reason.

    run_ingestion has NO dry_run parameter, so under --dry-run this
    stage is skipped outright rather than called. Calling it would hit
    Adzuna and insert rows, which is the opposite of what a dry run
    means.

    An ingestion failure does not stop the graph. Jobs already in the
    database are still scorable, and a source outage is not a reason to
    skip scoring the 99 rows that are already there.
    """
    if state.get("dry_run"):
        return _skip("discover_jobs", "dry_run")
    if state.get("skip_ingestion"):
        return _skip("discover_jobs", "skip_ingestion")

    async with AdzunaClient() as client:
        if not client.credentials_present():
            return _skip("discover_jobs", "credentials_missing")

        result = await run_ingestion(
            client,
            keywords=state.get("ingestion_keywords"),
            locations=state.get("ingestion_locations"),
            max_pages=state.get("ingestion_max_pages"),
        )

    normalised = normalise_ingestion_result(result)
    update: dict[str, Any] = {
        "ingestion": normalised,
        "stages_attempted": ["discover_jobs"],
        "stages_persisted": ["discover_jobs"],
    }
    if ingestion_computed(normalised):
        update["stages_computed"] = ["discover_jobs"]
    return update


async def embed_jobs(state: dict[str, Any]) -> dict[str, Any]:
    """Embed newly ingested jobs.

    NOT in the plan's Day 9 row, and mandatory anyway. run_scoring skips
    every job whose embedding is NULL and counts it in
    jobs_skipped_no_embedding; a graph that ingested twenty jobs and
    scored none of them would balance its funnel perfectly while doing
    it. That is a gap in the plan, not in the code.

    Runs BEFORE enrichment, though neither feeds the other --
    build_job_document() reads only title and description, so enrichment
    output never reaches a vector. The order matters for a different
    reason: enrichment is the stage that burns a daily quota and stops
    mid-loop, and embedding is the stage that decides whether new jobs
    are scorable at all. Embedding first means one 429 cannot make a
    whole ingest invisible to scoring.

    run_job_embedding has no dry_run parameter either, so --dry-run
    skips it rather than spending embedding quota.
    """
    if state.get("dry_run"):
        return _skip("embed_jobs", "dry_run")
    if state.get("skip_embedding"):
        return _skip("embed_jobs", "skip_embedding")

    result = await run_job_embedding()
    normalised = normalise_embedding_result(result)

    update: dict[str, Any] = {
        "embedding": normalised,
        "stages_attempted": ["embed_jobs"],
        "stages_persisted": ["embed_jobs"],
    }
    if embedding_computed(normalised):
        update["stages_computed"] = ["embed_jobs"]
    return update


async def enrich_jobs(state: dict[str, Any]) -> dict[str, Any]:
    """Extract skills and experience bounds, quota permitting.

    Unlike ingestion and embedding, run_enrichment DOES take dry_run,
    and under it counts every candidate without making a single API
    call. So a dry run calls this stage rather than skipping it.

    The quota case is the one that must not read as success.
    run_enrichment does not raise on a quota error -- it STOPS, backs
    the aborted job out of its counters, and returns normally with
    status QUOTA_EXCEEDED. A run that did essentially nothing therefore
    returns small numbers and an otherwise ordinary result. Reading the
    status rather than the counters is what keeps that from being
    reported as a clean, quiet night.
    """
    if state.get("skip_enrichment"):
        return _skip("enrich_jobs", "skip_enrichment")

    result = await run_enrichment(
        limit=state.get("enrichment_limit"),
        dry_run=bool(state.get("dry_run")),
    )
    normalised = normalise_enrichment_result(result)

    update: dict[str, Any] = {
        "enrichment": normalised,
        "stages_attempted": ["enrich_jobs"],
    }
    if normalised.get("status") == "quota_exceeded":
        update["stages_skipped"] = ["enrich_jobs: quota_exceeded"]
    if enrichment_computed(normalised):
        update["stages_computed"] = ["enrich_jobs"]
    if not state.get("dry_run"):
        update["stages_persisted"] = ["enrich_jobs"]
    return update


async def score_and_rank(state: dict[str, Any]) -> dict[str, Any]:
    """Match, score, rank, explain and decide eligibility -- in one call.

    Five of the plan's eleven nodes collapse here, and the collapse is
    the point. All five are steps inside a function that already owns a
    transaction per user and asserts its own funnel. Splitting them
    would mean either five transactions where there is one commit per
    user, or five nodes sharing a session across node boundaries. Both
    are worse than what exists.
    """
    result = await run_scoring(
        user_id=state.get("user_id"),
        dry_run=bool(state.get("dry_run")),
    )

    update: dict[str, Any] = {
        "scoring": result,
        "stages_attempted": ["score_and_rank"],
    }
    if scoring_computed(result):
        update["stages_computed"] = ["score_and_rank"]
    if not state.get("dry_run"):
        update["stages_persisted"] = ["score_and_rank"]
    if result.get("status") in SCORING_TERMINAL_STATUSES:
        update["terminal_reason"] = result["status"]
    return update


async def decide_notification(state: dict[str, Any]) -> dict[str, Any]:
    """Record which notification branch this run takes.

    Day 9 ends here. Delivery is Day 11, and no Telegram import belongs
    in this package. What this produces is an OBSERVABLE decision: a
    branch name and the count behind it, both in the summary.

    It applies no threshold of its own. run_scoring already counted
    notify_eligible using is_notify_eligible()'s three inclusive gates;
    re-deriving that here would be a second copy of the rule with
    nothing keeping the two in step.
    """
    scoring = state.get("scoring") or {}
    notify_eligible = int(scoring.get("notify_eligible") or 0)
    branch = route_notification({"notify_eligible": notify_eligible})

    return {
        "notify_eligible": notify_eligible,
        "notify_branch": branch,
        "stages_attempted": ["decide_notification"],
    }


async def finalise(state: dict[str, Any]) -> dict[str, Any]:
    """Stamp the end time. Nothing else.

    The summary is built by build_run_summary(), which reads no clock
    on purpose -- so the clock is read exactly once, here, and written
    into state before the pure function ever sees it.
    """
    return {
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "stages_attempted": ["finalise"],
    }
