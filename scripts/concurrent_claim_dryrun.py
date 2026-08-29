"""Prove the extraction claim actually excludes a second task.

    python -m scripts.concurrent_claim_dryrun --user-id 2

Fires two extractions at the same CV simultaneously and reports what
each returned. Day 4 wrote extraction_status='extracting' but never
read it back, so it recorded intent without preventing anything: two
tasks could extract one CV at once, both compute the same
MAX(version) + 1, and collide on uq_cv_version. Fix 4 made the write
conditional. This demonstrates the result rather than assuming it.

Expected: exactly one task returns 'complete' and the other returns
'extracting', having stood down. Two 'complete' results, or an
IntegrityError on uq_cv_version, means the claim is not working.

This makes one real Gemini call (the winner's), so it costs quota.
"""

import argparse
import asyncio
import sys

if sys.platform == "win32":
    # psycopg's async driver needs a selector event loop; Windows defaults
    # to the proactor loop, which it can't use.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import text

from app.db.session import dispose_engine, init_engine
from app.services.cv_extraction import extract_cv


async def attempt(label: str, user_id: int) -> tuple[str, str]:
    """Run one extraction and report its outcome without raising."""
    try:
        result = await extract_cv(user_id)
        return label, result.status.value
    except Exception as error:  # noqa: BLE001 - the failure IS the finding
        return label, f"RAISED {type(error).__name__}: {str(error)[:120]}"


async def version_count(engine, user_id: int) -> int:
    async with engine.connect() as connection:
        result = await connection.execute(
            text(
                "SELECT count(*) FROM cv_versions v "
                "JOIN cvs c ON c.id = v.cv_id WHERE c.user_id = :uid"
            ),
            {"uid": user_id},
        )
        return result.scalar_one()


async def run(user_id: int) -> None:
    engine = init_engine()
    if engine is None:
        print("DATABASE_URL is not configured.")
        return

    try:
        before = await version_count(engine, user_id)
        print(f"cv_versions rows before: {before}")
        print("Firing two extractions at the same CV...\n")

        # gather, not sequential awaits — the point is that both are in
        # flight at once. Run sequentially and the first would finish
        # and release its claim before the second ever looked.
        outcomes = await asyncio.gather(
            attempt("task A", user_id),
            attempt("task B", user_id),
        )

        for label, status in outcomes:
            print(f"  {label}: {status}")

        after = await version_count(engine, user_id)
        print(f"\ncv_versions rows after: {after} (+{after - before})")

        statuses = [status for _, status in outcomes]
        completed = statuses.count("complete")
        stood_down = statuses.count("extracting")

        print()
        if completed == 1 and stood_down == 1:
            print("PASS: one task won the claim, one stood down.")
        elif completed == 2:
            print("FAIL: both tasks extracted. The claim is not excluding.")
        else:
            print(f"INCONCLUSIVE: {statuses}. Re-run, or check the CV's status.")

        if after - before > 1:
            print("FAIL: more than one version row was created.")
    finally:
        await dispose_engine()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", type=int, required=True)
    asyncio.run(run(parser.parse_args().user_id))


if __name__ == "__main__":
    main()
