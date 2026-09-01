"""Run one job enrichment pass by hand.

    python -m scripts.enrich_jobs --dry-run
    python -m scripts.enrich_jobs
    python -m scripts.enrich_jobs --limit 8
    python -m scripts.enrich_jobs --retry-failed

Thin on purpose, mirroring scripts/embed_jobs.py: every rule lives in
app.services.job_enrichment, so Day 10 registers run_enrichment() with
a scheduler rather than dismantling this file.

CALLS A LIVE THIRD-PARTY API AND WRITES TO THE DATABASE.
Not to be run by an automated agent.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import statistics
import sys

if sys.platform == "win32":
    # This script opens a database connection. psycopg's async driver
    # cannot use the ProactorEventLoop Windows defaults to. Set before
    # anything imports the database layer.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.core.config import settings
from app.db.repositories.job import JobRepository
from app.db.session import dispose_engine, init_engine, session_scope
from app.services.job_enrichment import run_enrichment

# Bare-message formatting: this is a hand-run diagnostic script, not a
# service with a log aggregator behind it. Configured here, not in
# app.services.job_enrichment, so the per-job "job N: X.Xs (ok)" lines
# that module logs actually reach the terminal as the run progresses
# instead of being silently dropped by Python's default "no handler
# configured" behaviour. That per-job visibility is the whole point --
# Day 5 lost three hours to a call that hung rather than failing, and
# a line printed per job is what tells "slow" from "stuck" while the
# run is still going, not after it.
logging.basicConfig(level=logging.INFO, format="%(message)s")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Enrich stored jobs with skills.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Include rows that already failed. Use when the failure was the provider's.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the candidate count and API call budget, then exit without calling.",
    )
    args = parser.parse_args()

    if init_engine() is None:
        print("DATABASE_URL is not configured.")
        return 1

    try:
        if args.dry_run:
            async with session_scope() as session:
                repository = JobRepository(session)
                total = await repository.count_active_jobs()
                missing = await repository.count_active_missing_skills()

            result = await run_enrichment(
                limit=args.limit, retry_failed=args.retry_failed, dry_run=True
            )
            calls = result["would_attempt"]

            # A RANGE, not a single number. Fifteen timed calls during
            # isolation spanned 7.4s to 74.1s for requests that never
            # changed, and the spread was not driven by request size --
            # the smallest call in the set was repeatedly among the
            # slowest. A single estimate would advertise a precision
            # measurement has already disproved, and a run that looks
            # stuck against a false precise estimate is a run someone
            # kills halfway through for no reason.
            pace = settings.enrichment_seconds_between_calls
            low = calls * (10 + pace)
            high = calls * (45 + pace)

            print(f"model              {settings.gemini_model}")
            print(f"active jobs        {total}")
            print(f"missing skills     {missing}")
            print(f"would enrich       {calls}")
            print(f"skipped (empty)    {result['skipped_empty_text']}")
            print(f"API calls budget   {calls}")
            print(f"estimated time     {low:.0f}s .. {high:.0f}s")
            print()
            print("Dry run. No API call made, nothing written.")
            return 0

        result = await run_enrichment(limit=args.limit, retry_failed=args.retry_failed)

        print()
        print(f"status               {result['status']}")
        print(f"active jobs          {result['total_in_scope']}")
        print("--- funnel")
        print(f"candidates           {result['candidates_considered']}")
        print(f"skipped empty text   {result['skipped_empty_text']}")
        print(f"attempted            {result['attempted']}")
        print(f"succeeded            {result['succeeded']}")
        print(f"failed               {result['failed']}")
        print(f"id mismatches        {result['id_mismatches']}")
        print(f"API calls            {result['api_calls']}")
        print()
        print("--- skills")
        print(f"skills written       {result['total_skills_written']}")
        print(f"dropped (too long)   {result['total_dropped_too_long']}")
        print(f"dropped (soft)       {result['total_dropped_soft']}")
        print()
        print("--- work mode")
        print(f"remote               {result['work_mode_remote']}")
        print(f"hybrid               {result['work_mode_hybrid']}")
        print(f"none                 {result['work_mode_none']}")
        print()

        call_seconds = result["call_seconds"]
        if call_seconds:
            print("--- call timing")
            print(f"min call seconds     {min(call_seconds):.3f}")
            print(f"median call seconds  {statistics.median(call_seconds):.3f}")
            print(f"max call seconds     {max(call_seconds):.3f}")
            print()

        # Printed last and alone. This is the number that says whether
        # Day 8 can see every job's skills -- a job with none still
        # ranks, it just abstains on 30% of the model.
        print(f"STILL WITHOUT SKILLS: {result['remaining_null']}")

        if result["error_message"]:
            print()
            print(f"error: {result['error_message']}")

        return 0 if result["status"] in ("complete", "nothing_to_do") else 1

    finally:
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
