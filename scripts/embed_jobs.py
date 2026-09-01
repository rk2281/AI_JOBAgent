"""Run one job embedding pass by hand.

    python -m scripts.embed_jobs --dry-run
    python -m scripts.embed_jobs
    python -m scripts.embed_jobs --limit 8
    python -m scripts.embed_jobs --retry-failed
    python -m scripts.embed_jobs --recheck

Thin on purpose. Every rule is in app.services.job_embedding, so Day
10 registers run_job_embedding() with a scheduler rather than
dismantling this file.

CALLS A LIVE THIRD-PARTY API AND WRITES TO THE DATABASE.
Not to be run by an automated agent.
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
from app.db.repositories.job import JobRepository
from app.db.session import dispose_engine, init_engine, session_scope
from app.services.job_embedding import run_job_embedding


async def main() -> int:
    parser = argparse.ArgumentParser(description="Embed stored jobs.")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Include rows that already failed. Use when the failure was the provider's.",
    )
    parser.add_argument(
        "--recheck",
        action="store_true",
        help="Re-embed rows whose stored text hash no longer matches today's builder.",
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
                missing = await repository.count_active_missing_embedding()
                candidates = len(
                    await repository.list_needing_embedding(args.limit, args.retry_failed)
                )

            batch_size = settings.embedding_batch_size
            calls = -(-candidates // batch_size) if candidates else 0

            print(f"model              {settings.gemini_embedding_model}")
            print(f"dimension          {settings.embedding_dimension}")
            print(f"active jobs        {total}")
            print(f"missing embedding  {missing}")
            print(f"would embed        {candidates}")
            print(f"batch size         {batch_size}")
            print(f"API calls budget   {calls}")
            print()
            print("Dry run. No API call made, nothing written.")
            return 0

        result = await run_job_embedding(
            limit=args.limit,
            retry_failed=args.retry_failed,
            recheck=args.recheck,
        )

        print(f"status               {result.status.value}")
        print(f"active jobs          {result.total_in_scope}")
        print("--- funnel")
        print(f"candidates           {result.counters.candidates_considered}")
        print(f"skipped empty text   {result.counters.skipped_empty_text}")
        print(f"attempted            {result.counters.attempted}")
        print(f"succeeded            {result.counters.succeeded}")
        print(f"failed               {result.counters.failed}")
        print(f"abandoned            {result.counters.abandoned}")
        print(f"API calls            {result.counters.api_calls}")
        print(f"truncated            {result.truncated}")
        print(f"funnel balances      {result.counters.accounted_for()}")
        print()

        # Printed last and alone. This is the number that says whether
        # Day 8 can see every job -- a row with no vector is absent
        # from every similarity query rather than merely ranked low.
        print(f"STILL WITHOUT AN EMBEDDING: {result.remaining_null}")

        if result.remaining_null:
            print()
            print("Not zero. Find out which, and why:")
            print('  python -m scripts.query "SELECT id, embedding_attempts, '
                  'embedding_error FROM jobs WHERE is_active AND embedding IS NULL"')

        if result.error_message:
            print()
            print(f"error: {result.error_message}")

        return 0 if result.is_healthy and result.remaining_null == 0 else 1

    finally:
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
