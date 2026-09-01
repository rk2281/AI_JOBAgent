"""Search stored jobs by similarity, and verify the HNSW index.

    python -m scripts.search_jobs --self-check
    python -m scripts.search_jobs --user-id 2
    python -m scripts.search_jobs --user-id 2 --explain
    python -m scripts.search_jobs --text "machine learning engineer"

Only --text calls the provider. --user-id and --self-check are pure
database work, because both sides were embedded in Parts 4 and 5.

--text CALLS A LIVE THIRD-PARTY API. Not to be run by an automated
agent.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.db.repositories.cv import CVRepository
from app.db.repositories.job import JobRepository
from app.db.session import dispose_engine, init_engine, session_scope
from app.services.job_search import (
    DEFAULT_EF_SEARCH,
    search_by_text,
    search_for_user,
    self_check,
)


def _print_matches(matches) -> None:
    print(f"{'sim':>6}  {'id':>5}  title")
    print("-" * 78)
    for match in matches:
        title = (match.title or "")[:52]
        print(f"{match.similarity:6.4f}  {match.job_id:5d}  {title}")


async def _explain(vector: list[float], limit: int) -> None:
    """Print both plans, and say what each one proves.

    Two runs on purpose. The first shows what the planner chooses; at
    99 rows that is a sequential scan and it is the right choice. The
    second forces the issue and shows whether the index COULD serve
    the query at all.
    """
    async with session_scope() as session:
        repository = JobRepository(session)

        print("--- plan as chosen by the planner")
        for line in await repository.explain_nearest(vector, limit):
            print(f"    {line}")
        print()
        print("    A sequential scan here is CORRECT at 99 rows. Reading the")
        print("    whole table costs less than consulting an index.")
        print()

    async with session_scope() as session:
        repository = JobRepository(session)

        print("--- plan with enable_seqscan = off")
        for line in await repository.explain_nearest(
            vector, limit, disable_seqscan=True
        ):
            print(f"    {line}")
        print()
        print("    This one must name ix_jobs_embedding_hnsw. If it does, the")
        print("    operator, the opclass and the query shape all agree and the")
        print("    index will be used once the table is large enough to earn it.")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Search stored jobs.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--user-id", type=int, help="Match this user's stored CV.")
    group.add_argument("--text", help="Free text. COSTS ONE API CALL.")
    group.add_argument(
        "--self-check",
        action="store_true",
        help="Search with a job's own vector. It must return itself at 1.0.",
    )
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--ef-search", type=int, default=DEFAULT_EF_SEARCH)
    parser.add_argument(
        "--explain",
        action="store_true",
        help="Also print both query plans. Ignored with --text.",
    )
    args = parser.parse_args()

    if init_engine() is None:
        print("DATABASE_URL is not configured.")
        return 1

    try:
        if args.self_check:
            async with session_scope() as session:
                jobs = await JobRepository(session).list_active_for_recheck(1)

            if not jobs:
                print("No active jobs.")
                return 1

            outcome = await self_check(jobs[0].id, args.ef_search)
            for key, value in outcome.items():
                print(f"{key:18} {value}")
            return 0 if outcome.get("ok") else 1

        if args.user_id is not None:
            matches = await search_for_user(args.user_id, args.limit, args.ef_search)

            if matches is None:
                # Not the same as an empty list. Empty means searched
                # and found nothing; this means could not search.
                print(f"User {args.user_id} has no embedded active CV version.")
                print("Run: python -m scripts.embed_cvs")
                return 1

            print(f"Top {len(matches)} jobs for user {args.user_id}:")
            print()
            _print_matches(matches)

            if args.explain:
                print()
                async with session_scope() as session:
                    version = await CVRepository(
                        session
                    ).active_version_with_embedding(args.user_id)
                    vector = list(version.embedding)
                await _explain(vector, args.limit)

            return 0

        matches = await search_by_text(args.text, args.limit, ef_search=args.ef_search)
        print(f"Top {len(matches)} jobs for the given text:")
        print()
        _print_matches(matches)
        return 0

    finally:
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
