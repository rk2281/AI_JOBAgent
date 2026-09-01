"""Run one CV embedding pass by hand.

    python -m scripts.embed_cvs --dry-run
    python -m scripts.embed_cvs
    python -m scripts.embed_cvs --limit 8
    python -m scripts.embed_cvs --retry-failed

Thin on purpose. Every rule is in app.services.cv_embedding, so Day
10 registers run_cv_embedding() with a scheduler rather than
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
from app.db.repositories.cv import CVRepository
from app.db.session import dispose_engine, init_engine, session_scope
from app.services.cv_embedding import run_cv_embedding


async def main() -> int:
    parser = argparse.ArgumentParser(description="Embed active CV versions.")
    parser.add_argument("--limit", type=int, default=1000)
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
                repository = CVRepository(session)
                total = await repository.count_active_versions()
                missing = await repository.count_active_versions_missing_embedding()
                candidates = len(
                    await repository.list_active_versions_needing_embedding(
                        args.limit, args.retry_failed
                    )
                )

            # No batch size line: embed_query() sends one CV per call,
            # so the API call budget is simply the candidate count.
            print(f"model              {settings.gemini_embedding_model}")
            print(f"dimension          {settings.embedding_dimension}")
            print(f"active cv versions {total}")
            print(f"missing embedding  {missing}")
            print(f"would embed        {candidates}")
            print(f"API calls budget   {candidates}")
            print()
            print("Dry run. No API call made, nothing written.")
            return 0

        result = await run_cv_embedding(
            limit=args.limit,
            retry_failed=args.retry_failed,
        )

        print(f"status               {result.status.value}")
        print(f"active cv versions   {result.total_in_scope}")
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
        # Day 8 can see this candidate at all -- a version with no
        # vector is absent from every similarity query rather than
        # merely ranked low.
        print(f"STILL WITHOUT AN EMBEDDING: {result.remaining_null}")

        if result.remaining_null:
            print()
            print("Not zero. Find out which, and why:")
            print('  python -m scripts.query "SELECT id, embedding_attempts, '
                  'embedding_error FROM cv_versions WHERE embedding IS NULL '
                  'AND id IN (SELECT active_cv_version_id FROM profiles)"')

        if result.error_message:
            print()
            print(f"error: {result.error_message}")

        return 0 if result.is_healthy and result.remaining_null == 0 else 1

    finally:
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
