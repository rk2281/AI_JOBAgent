"""Run one job ingestion pass by hand.

    python -m scripts.ingest_jobs
    python -m scripts.ingest_jobs --keywords "nursing,teaching" --max-pages 1
    python -m scripts.ingest_jobs --dry-run

Deliberately thin. Every rule lives in app.services.job_ingestion, and
this file only parses arguments and prints. That is what makes Day 10
a matter of registering run_ingestion with APScheduler rather than a
rewrite: a script that owned the logic would have to be dismantled to
schedule it.

CALLS A LIVE THIRD-PARTY API. Not to be run by an automated agent.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

if sys.platform == "win32":
    # psycopg's async driver cannot use the ProactorEventLoop Windows
    # defaults to. Set before anything imports the database layer.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.core.config import settings
from app.db.session import dispose_engine, init_engine
from app.integrations.adzuna import AdzunaClient
from app.services.job_ingestion import run_ingestion


def _split(value: str | None) -> list[str] | None:
    if value is None:
        return None
    parsed = [item.strip() for item in value.split(",") if item.strip()]
    return parsed or [""]


async def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest jobs from Adzuna.")
    parser.add_argument(
        "--keywords",
        default=None,
        help="Comma-separated. Omit to use ADZUNA_QUERY_KEYWORDS.",
    )
    parser.add_argument(
        "--locations",
        default=None,
        help="Comma-separated. Omit to use ADZUNA_QUERY_LOCATIONS.",
    )
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan and the API call budget, then exit without calling.",
    )
    args = parser.parse_args()

    keywords = _split(args.keywords) or settings.adzuna_keyword_list
    locations = _split(args.locations) or settings.adzuna_location_list
    max_pages = args.max_pages or settings.adzuna_max_pages_per_run
    budget = len(keywords) * len(locations) * max_pages

    print("Ingestion plan")
    print(f"  keywords     : {keywords}")
    print(f"  locations    : {locations}")
    print(f"  max pages    : {max_pages}")
    print(f"  API calls    : up to {budget}")
    print("  (an empty string means no filter on that dimension)")

    if args.dry_run:
        print("\nDry run: no API call made.")
        return 0

    engine = init_engine()
    if engine is None:
        print("DATABASE_URL is not configured.")
        return 1

    try:
        async with AdzunaClient() as client:
            if not client.credentials_present():
                print("Adzuna credentials are not configured. Set them in .env.")
                return 1

            result = await run_ingestion(
                client,
                keywords=keywords,
                locations=locations,
                max_pages=max_pages,
            )
    finally:
        await dispose_engine()

    print(f"\nRun {result.run_id}: {result.status.value}")
    for name, value in result.counters.items():
        print(f"  {name:<20} {value}")

    if result.error:
        print(f"\n  error: {result.error}")

    # The funnel restated in words, because the counters alone do not
    # say which kind of zero this was.
    print()
    if result.status.value == "complete":
        print("  Healthy. New jobs, already-known jobs, or both.")
    elif result.status.value == "no_results":
        print("  The source returned nothing. Not a failure -- but several of")
        print("  these in a row means the query is too narrow.")
    elif result.status.value == "all_rejected":
        print("  Records arrived and NONE survived. Almost certainly our bug:")
        print("  check ingestion_rejects for the reasons.")
        print('  python -m scripts.query "SELECT stage, reason, count(*) '
              'FROM ingestion_rejects GROUP BY stage, reason"')
    elif result.status.value == "quota_exceeded":
        print("  Monthly quota spent. Not transient. Retrying will not help.")
    else:
        print("  The source could not be reached or answered badly.")

    return 0 if result.is_healthy else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
