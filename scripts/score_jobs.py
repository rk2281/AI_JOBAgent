"""Run one scoring pass by hand.

    python -m scripts.score_jobs --dry-run
    python -m scripts.score_jobs
    python -m scripts.score_jobs --user-id 2
    python -m scripts.score_jobs --user-id 2 --top 20 --bottom 10
    python -m scripts.score_jobs --user-id 2 --explain 481

Thin on purpose, mirroring scripts/embed_jobs.py and
scripts/enrich_jobs.py: every rule is in app.services.job_scoring and
app.services.scoring, so Day 10 registers run_scoring() with a
scheduler rather than dismantling this file.

--top and --bottom read recommendations.* directly; they do not
recompute anything, so they only make sense for a single --user-id
whose rows already exist (freshly written by this same invocation, or
left over from an earlier one). --explain JOB_ID likewise reads the
one stored row for --user-id and JOB_ID and prints its stored
signals, weight_covered, quality multiplier and final_score as
separate lines of arithmetic, so the number can be checked by hand
against combine()'s own docstring.

WRITES TO THE DATABASE unless --dry-run is given.
Not to be run by an automated agent.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

if sys.platform == "win32":
    # This script opens a database connection. psycopg's async driver
    # cannot use the ProactorEventLoop Windows defaults to. Set before
    # anything imports the database layer.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import select

from app.core.config import settings
from app.db.models.job import Job
from app.db.models.recommendation import Recommendation
from app.db.repositories.scoring import RecommendationRepository
from app.db.session import dispose_engine, init_engine, session_scope
from app.services.job_scoring import run_scoring

# ScoredPair's five signals, in the fixed order they are always
# reported -- same order combine() weights them in.
_SIGNAL_COLUMNS = (
    ("sem", "semantic_score"),
    ("skill", "skill_score"),
    ("exp", "experience_score"),
    ("loc", "location_score"),
    ("title", "title_score"),
)

_TITLE_WIDTH = 40


def _fmt(value: float | None, width: int = 6) -> str:
    """A score, right-aligned -- or "--" for an abstain.

    Printing an abstain as 0 would undo the entire distinction the
    nullable signal columns exist for, in the one place a human
    actually reads these numbers directly.
    """
    if value is None:
        return "--".rjust(width)
    return f"{value:.3f}".rjust(width)


async def _titles_for(session, job_ids: list[int]) -> dict[int, str]:
    if not job_ids:
        return {}
    result = await session.execute(select(Job.id, Job.title).where(Job.id.in_(job_ids)))
    return dict(result.all())


def _print_table(label: str, rows: list[Recommendation], titles: dict[int, str]) -> None:
    print(f"--- {label} ---")
    print(
        f"{'rank':>4} {'job_id':>7} {'final':>6} {'covered':>7} {'mult':>5} "
        f"{'sem':>6} {'skill':>6} {'exp':>6} {'loc':>6} {'title':>6}"
    )
    for row in rows:
        title = (titles.get(row.job_id) or "")[:_TITLE_WIDTH]
        print(
            f"{row.rank if row.rank is not None else -1:>4} "
            f"{row.job_id:>7} "
            f"{row.final_score:>6.3f} "
            f"{row.weight_covered:>7.3f} "
            f"{row.quality_multiplier:>5.3f} "
            f"{_fmt(row.semantic_score)} "
            f"{_fmt(row.skill_score)} "
            f"{_fmt(row.experience_score)} "
            f"{_fmt(row.location_score)} "
            f"{_fmt(row.title_score)}  "
            f"{title}"
        )
    print()


async def _explain(user_id: int, job_id: int) -> None:
    async with session_scope() as session:
        result = await session.execute(
            select(Recommendation).where(
                Recommendation.user_id == user_id, Recommendation.job_id == job_id
            )
        )
        row = result.scalar_one_or_none()
        job_result = await session.execute(select(Job.title).where(Job.id == job_id))
        title = job_result.scalar_one_or_none()

    print(f"--- explain: user {user_id}, job {job_id} ---")
    if row is None:
        print("No stored recommendation for this user/job pair.")
        print("Run scoring for this user first (without --dry-run).")
        return

    print(f"title:              {title}")
    print()
    print("signal        score   weight   contribution   reason")
    weights = {
        "semantic_score": settings.weight_semantic,
        "skill_score": settings.weight_skill,
        "experience_score": settings.weight_experience,
        "location_score": settings.weight_location,
        "title_score": settings.weight_title,
    }
    reasons_by_prefix = {r.split(":", 1)[0].strip(): r for r in row.match_reasons}
    for label, column in _SIGNAL_COLUMNS:
        value = getattr(row, column)
        weight = weights[column]
        contribution = "--" if value is None else f"{weight * value:.4f}"
        print(
            f"{label:<12}  {_fmt(value, 5)}   {weight:.2f}     {str(contribution).rjust(6)}"
        )
    print()
    print(f"weight_covered:     {row.weight_covered:.4f}")
    print(f"weighted_total:     {row.final_score / row.quality_multiplier if row.quality_multiplier else 0:.4f}"
          "   (final_score / quality_multiplier)")
    print(f"quality_multiplier: {row.quality_multiplier:.4f}")
    print(f"final_score:        {row.final_score:.4f}")
    print(f"semantic_raw:       {row.semantic_raw}")
    print(f"rank:               {row.rank}")
    print()
    print("match_reasons:")
    for reason in row.match_reasons:
        print(f"  - {reason}")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Score stored jobs against stored profiles.")
    parser.add_argument("--user-id", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--bottom", type=int, default=5)
    parser.add_argument("--explain", type=int, default=None, metavar="JOB_ID")
    args = parser.parse_args()

    if init_engine() is None:
        print("DATABASE_URL is not configured.")
        return 1

    try:
        result = await run_scoring(user_id=args.user_id, dry_run=args.dry_run)

        print(f"status                    {result['status']}")
        print(f"run_id                    {result['run_id']}")
        print("--- funnel: users")
        print(f"users_considered          {result['users_considered']}")
        print(f"users_skipped_no_cv       {result['users_skipped_no_cv']}")
        # The breakdown of the line above. Only cv_not_embedded is
        # fixable by running the embedding pass, so a person reading
        # this needs to see which cause applied, not just the total.
        print(f"  no_profile              {result['users_skipped_no_profile']}")
        print(f"  no_active_cv            {result['users_skipped_no_active_cv']}")
        print(f"  cv_not_embedded         {result['users_skipped_cv_not_embedded']}")
        print(f"users_scored              {result['users_scored']}")
        print("--- funnel: jobs")
        print(f"jobs_considered           {result['jobs_considered']}")
        print(f"jobs_skipped_no_embedding {result['jobs_skipped_no_embedding']}")
        print(f"jobs_excluded_manual      {result['jobs_excluded_manual']}")
        print(f"jobs_scored               {result['jobs_scored']}")
        print(f"pairs_scored              {result['pairs_scored']}")
        print("--- nearest_to drops")
        print(f"nearest_dropped_excluded  {result['nearest_dropped_excluded']}")
        print(f"nearest_dropped_unscorable {result['nearest_dropped_unscorable']}")
        print("--- abstains")
        print(f"abstain_semantic          {result['abstain_semantic']}")
        print(f"abstain_skill             {result['abstain_skill']}")
        print(f"abstain_experience        {result['abstain_experience']}")
        print(f"abstain_location          {result['abstain_location']}")
        print(f"abstain_title             {result['abstain_title']}")
        print("--- semantic clamps")
        print(f"semantic_clamped_low      {result['semantic_clamped_low']}")
        print(f"semantic_clamped_high     {result['semantic_clamped_high']}")
        print(f"semantic_raw_min          {result['semantic_raw_min']}")
        print(f"semantic_raw_median       {result['semantic_raw_median']}")
        print(f"semantic_raw_max          {result['semantic_raw_max']}")
        print("--- quality penalties")
        print(f"quality_penalty_agency    {result['quality_penalty_agency']}")
        print(f"quality_penalty_no_city   {result['quality_penalty_no_city']}")
        print("--- work mode")
        print(f"jobs_remote               {result['jobs_remote']}")
        print(f"jobs_hybrid               {result['jobs_hybrid']}")
        print("--- score distribution")
        print(f"score_min                 {result['score_min']}")
        print(f"score_median              {result['score_median']}")
        print(f"score_max                 {result['score_max']}")
        print(f"distinct_score_count      {result['distinct_score_count']}")
        print(f"notify_eligible           {result['notify_eligible']}")
        print()

        if args.user_id is None:
            print("No --user-id given: skipping top/bottom/explain output.")
            return 0

        if args.dry_run:
            print("--dry-run: nothing was written, so --top/--bottom/--explain")
            print("would only show stale data from an earlier run. Skipped.")
            return 0

        async with session_scope() as session:
            repository = RecommendationRepository(session)
            top_rows = await repository.top_for_user(args.user_id, args.top)
            bottom_rows = await repository.bottom_for_user(args.user_id, args.bottom)
            titles = await _titles_for(
                session, [row.job_id for row in (*top_rows, *bottom_rows)]
            )

        _print_table(f"top {args.top}", top_rows, titles)
        _print_table(f"bottom {args.bottom}", bottom_rows, titles)

        if args.explain is not None:
            await _explain(args.user_id, args.explain)

        return 0

    finally:
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
