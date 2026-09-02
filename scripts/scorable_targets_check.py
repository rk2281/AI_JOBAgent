"""Does resolve_scoring_targets() agree with what scoring actually does?

run_scoring() and resolve_scoring_targets() deliberately share one
definition of "scorable" -- is_scorable_user() -- so that the workflow
graph can never stop on a rule that differs from the rule scoring
applies. That sharing is the point, and it is also why comparing the
two functions to each other proves nothing: they would agree even if
the shared predicate were wrong.

So this script does not compare them to each other. It compares both to
an INDEPENDENT oracle: raw SQL, written from the schema, importing
neither is_scorable_user nor select_target_user_ids and touching
neither ProfileRepository nor CVRepository. The only thing it shares
with the code under test is the database.

That is the same exception scoring_isolate.py carves out for its
hand-computed cosine similarity: "verification math, not a second
implementation of anything." A cross-check oracle is allowed to
duplicate a rule precisely because a drift between the two is the
signal it exists to produce. Nothing in app/ may import from here.

WHY THE COMPARISON IS CONDITIONAL

run_scoring() enters its user loop only when jobs_scored > 0. On a day
with no scorable job, users_scored is 0 no matter how many people have
an embedded CV -- so asserting equality unconditionally would report a
failure that is really an empty jobs table. When jobs_scored is 0 this
prints "not comparable" and says why, rather than failing.

FOUR STATES, TWO OF WHICH LOOK THE SAME TO SCORING

A user reaches scoring in one of four states, and active_version_with
_embedding() collapses the middle two into a single None:

  1. no profile row
  2. profile, but active_cv_version_id IS NULL
  3. profile and active version, but that version has no embedding
  4. profile and an embedded active version          <- the only scorable one

run_scoring counts 1, 2 and 3 as one users_skipped_no_cv. Only state 3
is fixable by running the embedding pass. This script prints all four
separately, which is the only place that distinction is currently
visible.

Runs run_scoring with dry_run=True: every counter is computed, no
scoring_runs row and no recommendations rows are written. Reads only.

    python -m scripts.scorable_targets_check
    python -m scripts.scorable_targets_check --user-id 2
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import text

from app.db.session import dispose_engine, init_engine, session_scope
from app.services.job_scoring import resolve_scoring_targets, run_scoring

# The independent oracle. Written from the schema (profiles.
# active_cv_version_id -> cv_versions.id, cv_versions.embedding), NOT
# from the code it checks. If this ever disagrees with
# resolve_scoring_targets(), one of the two is wrong and the run says
# so instead of averaging them.
_ORACLE_SCORABLE = """
SELECT p.user_id
FROM profiles p
JOIN cv_versions v ON v.id = p.active_cv_version_id
WHERE v.embedding IS NOT NULL
ORDER BY p.user_id
"""

# The four states, counted separately. Deliberately not derived from
# the query above -- each is its own predicate so a wrong join in one
# does not quietly fix the count in another.
_ORACLE_STATES = """
SELECT
  (SELECT count(*) FROM profiles)                                   AS profiles_total,
  (SELECT count(*) FROM profiles
     WHERE active_cv_version_id IS NULL)                            AS no_active_version,
  (SELECT count(*) FROM profiles p
     JOIN cv_versions v ON v.id = p.active_cv_version_id
    WHERE v.embedding IS NULL)                                      AS active_version_unembedded,
  (SELECT count(*) FROM profiles p
     JOIN cv_versions v ON v.id = p.active_cv_version_id
    WHERE v.embedding IS NOT NULL)                                  AS active_version_embedded
"""


def _print_header(label: str) -> None:
    print(f"\n--- {label} ---")


async def run(user_id: int | None) -> int:
    async with session_scope() as session:
        oracle_ids = [row[0] for row in (await session.execute(text(_ORACLE_SCORABLE))).all()]
        states = (await session.execute(text(_ORACLE_STATES))).one()

    if user_id is not None:
        oracle_ids = [uid for uid in oracle_ids if uid == user_id]

    _print_header("Independent oracle: the four states a user can be in")
    print(f"  profiles total                          : {states.profiles_total}")
    print(f"  1+2. no profile / no active CV version  : {states.no_active_version}")
    print(f"  3. active version, not embedded         : {states.active_version_unembedded}")
    print(f"  4. active version, embedded (SCORABLE)  : {states.active_version_embedded}")
    print("  (states 2 and 3 are one users_skipped_no_cv to scoring;")
    print("   only state 3 is fixable by running the embedding pass)")

    resolved = await resolve_scoring_targets(user_id=user_id)

    _print_header("resolve_scoring_targets() vs the oracle")
    print(f"  oracle scorable user ids   : {oracle_ids}")
    print(f"  resolver target_user_ids   : {resolved['target_user_ids']}")
    print(f"  oracle count               : {len(oracle_ids)}")
    print(f"  resolver users_with_embedded_cv : {resolved['users_with_embedded_cv']}")

    if sorted(oracle_ids) != sorted(resolved["target_user_ids"]):
        print("  MISMATCH: the resolver and the independent oracle disagree.")
        print("  Do not adjust this script. One of the two is wrong.")
        return 1
    print("  AGREE.")

    scoring = await run_scoring(user_id=user_id, dry_run=True)

    _print_header("run_scoring(dry_run=True) vs the oracle")
    print(f"  jobs_scored          : {scoring['jobs_scored']}")
    print(f"  users_considered     : {scoring['users_considered']}")
    print(f"  users_skipped_no_cv  : {scoring['users_skipped_no_cv']}")
    print(f"  users_scored         : {scoring['users_scored']}")

    if scoring["jobs_scored"] == 0:
        print("  NOT COMPARABLE: run_scoring only enters its user loop when")
        print("  jobs_scored > 0, so users_scored is 0 for a reason that has")
        print("  nothing to do with target resolution.")
        return 0

    if scoring["users_scored"] != len(oracle_ids):
        print(f"  MISMATCH: users_scored {scoring['users_scored']} != oracle {len(oracle_ids)}.")
        print("  Do not adjust this script. Scoring and the schema disagree.")
        return 1

    print("  AGREE: scoring scored exactly the users the oracle calls scorable.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Read-only scorable-target cross-check.")
    parser.add_argument("--user-id", type=int, default=None)
    args = parser.parse_args()

    if init_engine() is None:
        print("DATABASE_URL is not configured.")
        raise SystemExit(1)

    # psycopg's async driver cannot use the ProactorEventLoop Windows
    # defaults to -- same preamble as scoring_isolate.py.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        exit_code = asyncio.run(run(args.user_id))
    finally:
        asyncio.run(dispose_engine())

    raise SystemExit(exit_code)
