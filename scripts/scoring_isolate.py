"""Separate the four causes of a wrong scoring number, one stage at a time.

When run_scoring() produces a score that looks wrong, there are four
places the fault could be: the skills catalog, the weights, the
embeddings, or the scoring code itself. This script exists to tell
those apart instead of guessing which one to fix next.

It was not needed during Day 8 because that separation was done by
hand, one query and one REPL call at a time. It will be needed the
next time a number looks wrong, when nobody remembers how that was
done. This is that separation, made repeatable.

Every function called below is imported from the real modules --
app.services.scoring_signals, app.services.scoring,
app.db.repositories.job -- never reimplemented here. A copy that
drifts from the real code proves nothing about the real code; see
enrichment_isolate.py importing ENRICHMENT_SCHEMA for the same reason.
The one exception is the cosine similarity computed in stage 2, which
exists ONLY to cross-check that nearest_to()'s pgvector `<=>` distance
means what job_scoring.py assumes it means (raw_similarity = 1 -
distance) -- it is verification math, not a second implementation of
anything scoring.py or scoring_signals.py does.

Five stages, printed in order:

  1. Weights   -- all five weights, their sum, weights_version.
  2. Embeddings -- both vectors' dimension and L2 norm, the cosine
     distance nearest_to() itself reports for this pair, the raw
     similarity run_scoring() would derive from it, and an
     independently computed cosine similarity to cross-check that
     derivation.
  3. Skills catalog -- the job's stored skill keys, the profile's
     skill keys, their intersection, and the two set sizes. No score.
  4. The code -- each of the five scorers, called directly, printing
     value and reason. None prints as "--", never as 0 -- see
     scoring_signals.py's own module docstring for why conflating
     those two would be wrong.
  5. Combine -- weight_covered, weighted_total, quality_multiplier,
     final_score, and every entry of match_reasons.

No writes. No API calls. Only reads.

    python -m scripts.scoring_isolate --job-id 88 --user-id 2
"""

from __future__ import annotations

import argparse
import asyncio
import math
import sys

from app.core.config import settings
from app.db.repositories.cv import CVRepository
from app.db.repositories.job import JobRepository
from app.db.repositories.profile import ProfileRepository
from app.db.repositories.user import UserRepository
from app.db.session import dispose_engine, init_engine, session_scope
from app.services.scoring import assess_quality, combine
from app.services.scoring_signals import (
    score_experience,
    score_location,
    score_semantic,
    score_skill,
    score_title,
)

# Matches job_scoring.py's own floor -- scoring asks nearest_to() for
# every scorable job, not a top-K search, so ef_search must scale past
# pgvector's default of 40 the same way the real run does. A smaller
# value here would let this script's own cross-check silently return
# fewer candidates than exist, which is the exact failure mode Day 8's
# nearest_to()/is_excluded prompt was written to close.
_MIN_EF_SEARCH = 64


def _fmt(value: float | None) -> str:
    """None prints as "--", never as 0 -- see the module docstring."""
    return "--" if value is None else f"{value:.4f}"


def _l2_norm(vector: list[float]) -> float:
    return math.sqrt(sum(component * component for component in vector))


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    """Independent cross-check only -- see the module docstring."""
    left_norm = _l2_norm(left)
    right_norm = _l2_norm(right)
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    return dot / (left_norm * right_norm)


def _print_header(label: str) -> None:
    print(f"--- {label} ---")


async def run(job_id: int, user_id: int) -> int:
    async with session_scope() as session:
        job_repository = JobRepository(session)
        job = await job_repository.by_id(job_id)
        profile = await ProfileRepository(session).get_by_user_id(user_id)
        version = (
            await CVRepository(session).active_version_with_embedding(user_id)
            if profile is not None
            else None
        )
        preferences = await UserRepository(session).get_preferences(user_id)

        if job is None:
            print(f"No job with id {job_id}.")
            return 1
        if profile is None:
            print(f"No profile for user {user_id}.")
            return 1
        if version is None:
            print(f"User {user_id} has no active CV version with an embedding.")
            return 1
        if job.embedding is None:
            print(f"Job {job_id} has no embedding.")
            return 1

        # --- stage 1: weights ------------------------------------------
        _print_header("1. weights")
        weights = {
            "weight_semantic": settings.weight_semantic,
            "weight_skill": settings.weight_skill,
            "weight_experience": settings.weight_experience,
            "weight_location": settings.weight_location,
            "weight_title": settings.weight_title,
        }
        for name, value in weights.items():
            print(f"    {name:<20} {value:.4f}")
        print(f"    {'sum':<20} {sum(weights.values()):.4f}")
        print(f"    {'weights_version':<20} {settings.weights_version}")
        print()

        # --- stage 2: embeddings -----------------------------------------
        _print_header("2. embeddings")
        job_vector = list(job.embedding)
        cv_vector = list(version.embedding)

        print(f"    job vector dimension       {len(job_vector)}")
        print(f"    cv vector dimension        {len(cv_vector)}")
        print(f"    job vector l2_norm         {_l2_norm(job_vector):.6f}")
        print(f"    cv vector l2_norm          {_l2_norm(cv_vector):.6f}")

        # nearest_to() is the real code path -- the same query and the
        # same `<=>` operator run_scoring() uses. limit is the pool size
        # nearest_to() actually searches, exactly as job_scoring.py now
        # asks for it, so job_id is guaranteed to appear in the results
        # rather than being cut off by an undersized limit.
        pool_size = await job_repository.count_active_embedded_jobs()
        ef_search = max(pool_size, _MIN_EF_SEARCH)
        nearest = await job_repository.nearest_to(cv_vector, limit=pool_size, ef_search=ef_search)

        match = next((entry for entry in nearest if entry[0].id == job_id), None)
        if match is None:
            print(f"    job {job_id} did not appear in nearest_to()'s own result set.")
            print("    It is either inactive, unembedded, or excluded -- nearest_to()")
            print("    does not filter is_excluded, so exclusion alone would not")
            print("    explain this. Check is_active and embedding IS NOT NULL.")
            return 1

        _, cosine_distance = match
        raw_similarity_via_nearest_to = 1.0 - cosine_distance
        raw_similarity_independent = _cosine_similarity(job_vector, cv_vector)

        print(f"    cosine distance (nearest_to)         {cosine_distance:.6f}")
        print(f"    raw similarity (1 - distance)         {raw_similarity_via_nearest_to:.6f}")
        print(f"    raw similarity (independent cosine)   {raw_similarity_independent:.6f}")
        print(
            "    difference                            "
            f"{abs(raw_similarity_via_nearest_to - raw_similarity_independent):.6f}"
        )
        print()

        # --- stage 3: skills catalog -------------------------------------
        _print_header("3. skills catalog")
        skills_by_job = await job_repository.skills_for_jobs([job_id])
        job_skills = sorted(skills_by_job.get(job_id, []))
        profile_skills = sorted(profile.skills or [])
        intersection = sorted(set(job_skills) & set(profile_skills))

        print(f"    job skills       {job_skills}")
        print(f"    profile skills   {profile_skills}")
        print(f"    intersection     {intersection}")
        print(f"    job skill count       {len(job_skills)}")
        print(f"    profile skill count   {len(profile_skills)}")
        print()

        # --- stage 4: the code ---------------------------------------------
        _print_header("4. the five scorers")
        target_roles = preferences.target_roles if preferences else []
        preferred_locations = preferences.preferred_locations if preferences else []
        remote_only = preferences.remote_only if preferences else False

        semantic = score_semantic(raw_similarity_via_nearest_to)
        skill = score_skill(job_skills, profile_skills)
        experience = score_experience(
            profile.total_experience_years,
            job.min_experience_years,
            job.max_experience_years,
        )
        location = score_location(job.location, job.work_mode, preferred_locations, remote_only)
        title = score_title(job.title, target_roles)

        for label, signal in (
            ("semantic", semantic),
            ("skill", skill),
            ("experience", experience),
            ("location", location),
            ("title", title),
        ):
            print(f"    {label:<12} value={_fmt(signal.value):<8} reason={signal.reason}")
        print()

        # --- stage 5: combine ----------------------------------------------
        _print_header("5. combine")
        quality = assess_quality(job.company, job.location)
        scored = combine(
            semantic=semantic,
            skill=skill,
            experience=experience,
            location=location,
            title=title,
            semantic_raw=raw_similarity_via_nearest_to,
            quality=quality,
        )

        print(f"    weight_covered       {scored.weight_covered:.4f}")
        print(f"    weighted_total       {scored.weighted_total:.4f}")
        print(f"    quality_multiplier   {scored.quality.multiplier:.4f}")
        print(f"    final_score          {scored.final_score:.4f}")
        print("    match_reasons:")
        for reason in scored.match_reasons:
            print(f"      - {reason}")

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-id", type=int, required=True)
    parser.add_argument("--user-id", type=int, required=True)
    args = parser.parse_args()

    if init_engine() is None:
        print("DATABASE_URL is not configured.")
        raise SystemExit(1)

    # psycopg's async driver cannot use the ProactorEventLoop Windows
    # defaults to -- same reason enrichment_isolate.py's --job-id mode
    # sets this, and only there, since this script always opens a
    # database connection.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        exit_code = asyncio.run(run(args.job_id, args.user_id))
    finally:
        asyncio.run(dispose_engine())

    raise SystemExit(exit_code)
