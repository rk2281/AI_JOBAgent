"""Drive the Day 9 workflow by hand.

    python -m scripts.run_agent --dry-run
    python -m scripts.run_agent --user-id 2 --dry-run
    python -m scripts.run_agent --skip-ingestion --skip-enrichment
    python -m scripts.run_agent

Thin on purpose, in the same shape as scripts/score_jobs.py and
scripts/enrich_jobs.py: every rule lives in app/workflows/, so Day 10
registers the compiled graph with APScheduler rather than dismantling
this file.

WHAT --dry-run ACTUALLY MEANS HERE, because it is not what it looks like

Only two of the four services take a dry_run parameter. run_enrichment
counts candidates without calling the API; run_scoring computes every
signal and writes neither a scoring_runs row nor any recommendations.
run_ingestion and run_job_embedding take no such parameter at all, so
under --dry-run they are SKIPPED rather than called -- calling them
would hit Adzuna, insert rows and spend embedding quota, which is the
opposite of a rehearsal.

So --dry-run is a SCORING rehearsal, not a pipeline rehearsal. It scores
whatever is already in the database. Read it as "what would scoring say
about today's rows", never as "what tonight's run would do" -- the
difference is exactly the jobs ingestion would have added.

WRITES TO THE DATABASE unless --dry-run is given.

RESOURCE LIFECYCLE

The engine is opened and disposed here, around the whole run, and never
enters graph state. AdzunaClient is opened and closed INSIDE the
discover_jobs node rather than here, because a client held open across
the graph would stay open through enrichment's 27-84 minutes for no
benefit.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

if sys.platform == "win32":
    # This script opens a database connection. psycopg's async driver
    # cannot use the ProactorEventLoop Windows defaults to. Set BEFORE
    # anything imports the database layer -- app.workflows.graph pulls
    # in the services, which pull in app.db.session, so doing this
    # under __main__ would already be too late.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from datetime import datetime, timezone  # noqa: E402

from app.db.session import dispose_engine, init_engine  # noqa: E402
from app.workflows.graph import build_graph  # noqa: E402
from app.workflows.state import build_run_summary, initial_state  # noqa: E402

# Printed as their own block so a reader sees the four lists before the
# counters, rather than hunting for a skip reason among thirty numbers.
_LIST_FIELDS = (
    "stages_attempted",
    "stages_skipped",
    "stages_computed",
    "stages_persisted",
    "errors",
)


def _print_summary(summary: dict) -> None:
    print(f"status                    {summary['status']}")
    print(f"dry_run                   {summary['dry_run']}")
    print(f"started_at                {summary['started_at']}")
    print(f"finished_at               {summary['finished_at']}")

    print("--- what happened")
    print(f"computation_performed     {summary['computation_performed']}")
    print(f"persistence_performed     {summary['persistence_performed']}")
    print(f"writes_prevented          {summary['writes_prevented']}")

    for field in _LIST_FIELDS:
        values = summary[field] or ["(none)"]
        print(f"--- {field}")
        for value in values:
            print(f"  {value}")

    print("--- targets")
    for key in ("users_considered", "users_with_profile", "users_with_embedded_cv"):
        print(f"{key:25} {summary[key]}")

    print("--- stages")
    for key in (
        "ingestion_status",
        "ingestion_run_id",
        "jobs_inserted",
        "embedding_status",
        "jobs_embedded",
        "embeddings_remaining_null",
        "enrichment_status",
        "jobs_enriched",
        "enrichment_remaining_null",
        "scoring_status",
        "scoring_run_id",
        "users_skipped_no_cv",
        "users_skipped_no_profile",
        "users_skipped_no_active_cv",
        "users_skipped_cv_not_embedded",
        "users_scored",
        "jobs_scored",
        "pairs_scored",
        "jobs_skipped_no_embedding",
    ):
        print(f"{key:25} {summary[key]}")

    print("--- notification")
    print(f"notify_branch             {summary['notify_branch']}")
    print(f"notify_eligible           {summary['notify_eligible']}")
    print(f"terminal_reason           {summary['terminal_reason']}")


async def run(args: argparse.Namespace) -> int:
    state = initial_state(
        user_id=args.user_id,
        dry_run=args.dry_run,
        skip_ingestion=args.skip_ingestion,
        skip_embedding=args.skip_embedding,
        skip_enrichment=args.skip_enrichment,
        ingestion_keywords=args.keywords,
        ingestion_locations=args.locations,
        ingestion_max_pages=args.max_pages,
        enrichment_limit=args.enrichment_limit,
        started_at=datetime.now(timezone.utc).isoformat(),
    )

    final = await build_graph().ainvoke(state)
    summary = build_run_summary(final)
    _print_summary(summary)

    # A non-zero exit for the two statuses that mean something went
    # wrong, so a scheduler on Day 10 can tell them from a quiet night
    # without parsing this output.
    return 1 if summary["status"] in ("failed", "degraded") else 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Day 9 workflow once.")
    parser.add_argument("--user-id", type=int, default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scoring rehearsal: skips ingestion and embedding, writes nothing.",
    )
    parser.add_argument("--skip-ingestion", action="store_true")
    parser.add_argument("--skip-embedding", action="store_true")
    parser.add_argument("--skip-enrichment", action="store_true")
    parser.add_argument("--keywords", nargs="*", default=None)
    parser.add_argument("--locations", nargs="*", default=None)
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument("--enrichment-limit", type=int, default=None)
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    if init_engine() is None:
        print("DATABASE_URL is not configured.")
        raise SystemExit(1)

    try:
        exit_code = asyncio.run(run(args))
    finally:
        asyncio.run(dispose_engine())

    raise SystemExit(exit_code)
