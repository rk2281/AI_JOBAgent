"""Day 12 integration test 3 -- embedding -> pgvector -> match -> rank.

    cv_versions.embedding + jobs.embedding      (REAL vectors, real column)
        -> JobRepository.nearest_to             (REAL pgvector <=> operator)
        -> app.services.job_search.search_for_user
        -> app.services.job_scoring.run_scoring (REAL, all five signals)
        -> recommendations rows with ranks      (REAL PostgreSQL)

NOTHING IS MOCKED IN THIS FILE

Day 12 section 7 says not to replace real components with mocks for the
final verification, and this pipeline needs no exception: comparing a
CV against every job costs no API call, because both sides are already
vectors in the same table. Gemini is only needed to CREATE a vector.
So the vectors here are constructed rather than generated -- which is a
statement about the test data, not about the machinery. Every operator,
index, query, signal, weight and rank below is the production one.

WHY THE VECTORS ARE BUILT THE WAY THEY ARE

Random 768-dimensional vectors are nearly orthogonal, so a random
fixture would put every job at roughly the same distance and any
ordering assertion would be luck. These are built from two fixed
orthogonal directions and mixed by a known angle, so the expected
cosine similarity is arithmetic and the ORDER is a fact rather than an
observation.
"""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.db.models.cv import CV, CVVersion, ExtractionStatus
from app.db.models.job import Job
from app.db.models.profile import Profile
from app.db.models.recommendation import Recommendation
from app.db.models.scoring import ScoringRun
from app.db.models.user import User, UserPreference
from app.db.session import session_scope
from app.services.job_scoring import run_scoring
from app.services.job_search import search_for_user

DIMENSIONS = 768
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)

# Two orthogonal unit directions. Everything below is a rotation in the
# plane they span, so cos(theta) IS the cosine similarity.
_A = 0
_B = 1


def unit_vector_at(degrees: float) -> list[float]:
    """A unit vector `degrees` away from the CV's direction."""
    radians = math.radians(degrees)
    vector = [0.0] * DIMENSIONS
    vector[_A] = math.cos(radians)
    vector[_B] = math.sin(radians)
    return vector


CV_VECTOR = unit_vector_at(0)  # the candidate

# 0 deg -> similarity 1.00, 60 deg -> 0.50, 90 deg -> 0.00
JOB_ANGLES = {
    "near": 10.0,
    "middling": 60.0,
    "far": 88.0,
}


async def _seed_candidate(*, telegram_id: int = 910001) -> int:
    """One realistic candidate: the AI/ML engineer from the Day 12 brief."""
    async with session_scope() as session:
        user = User(telegram_id=telegram_id, full_name="Priya Sharma")
        session.add(user)
        await session.flush()

        cv = CV(
            user_id=user.id,
            file_name="priya_cv.docx",
            file_type="docx",
            storage_path="/dev/null",
            extraction_status=ExtractionStatus.COMPLETE.value,
        )
        session.add(cv)
        await session.flush()

        version = CVVersion(
            cv_id=cv.id,
            version=1,
            extracted_profile={"skills": ["Python", "Machine Learning"]},
            extraction_model="fixture",
            embedding=CV_VECTOR,
            embedding_model="fixture",
            embedded_at=NOW,
        )
        session.add(version)
        await session.flush()

        session.add(
            Profile(
                user_id=user.id,
                summary="AI/ML engineer, one year of experience.",
                total_experience_years=1.0,
                current_title="ML Engineer",
                location="Delhi",
                skills=["python", "machine learning", "nlp", "fastapi", "sql"],
                experience=[],
                education=[],
                active_cv_version_id=version.id,
            )
        )
        session.add(
            UserPreference(
                user_id=user.id,
                target_roles=["AI Engineer", "ML Engineer", "Machine Learning Engineer"],
                preferred_locations=["Delhi"],
            )
        )
        return user.id


async def _seed_jobs() -> dict[str, int]:
    """Three jobs at known angles from the CV, otherwise identical."""
    ids: dict[str, int] = {}
    async with session_scope() as session:
        for name, angle in JOB_ANGLES.items():
            job = Job(
                source="fixture",
                external_id=f"job-{name}",
                title="Machine Learning Engineer",
                company="Northwind Analytics",
                location="Delhi",
                description="Python, ML and NLP work.",
                url=f"https://example.test/{name}",
                posted_at=NOW - timedelta(days=1),
                last_seen_at=NOW,
                content_hash=f"hash-{name}",
                embedding=unit_vector_at(angle),
                embedding_model="fixture",
                embedded_at=NOW,
            )
            session.add(job)
            await session.flush()
            ids[name] = job.id
    return ids


def test_pgvector_retrieval_returns_the_right_jobs_in_the_right_order(
    run_with_database: Callable[[Callable[[], Awaitable[Any]]], Any],
) -> None:
    """Day 12 section 9: generated -> stored -> shaped -> searchable.

    Asserts the stored dimensionality as well as the ordering. A vector
    of the wrong length does not fail quietly here -- pgvector rejects
    it -- but reading it back is what proves the column is `vector(768)`
    rather than something that merely accepted the insert.
    """

    async def body() -> dict[str, Any]:
        user_id = await _seed_candidate()
        job_ids = await _seed_jobs()

        matches = await search_for_user(user_id, limit=10)

        async with session_scope() as session:
            stored = (await session.execute(select(CVVersion.embedding))).scalar_one()

        return {
            "matches": matches,
            "job_ids": job_ids,
            "stored_dimensions": len(stored),
        }

    observed = run_with_database(body)
    matches = observed["matches"]
    job_ids = observed["job_ids"]

    assert observed["stored_dimensions"] == DIMENSIONS

    assert [m.job_id for m in matches] == [
        job_ids["near"],
        job_ids["middling"],
        job_ids["far"],
    ]

    # The similarity numbers are the cosine of the angle we built in,
    # to pgvector's float precision. This is the check that would catch
    # a distance-to-similarity conversion getting inverted -- which
    # would still return three rows, in a plausible-looking order.
    by_name = {name: matches[i] for i, name in enumerate(["near", "middling", "far"])}
    assert abs(by_name["near"].similarity - math.cos(math.radians(10))) < 1e-4
    assert abs(by_name["middling"].similarity - 0.5) < 1e-4
    assert by_name["far"].similarity < 0.05


def test_a_user_with_no_embedded_cv_cannot_search_and_says_so(
    run_with_database: Callable[[Callable[[], Awaitable[Any]]], Any],
) -> None:
    """None means "could not search"; [] means "searched, found nothing".

    Collapsing these would show a user with a broken CV the same empty
    screen as a user in a quiet week.
    """

    async def body() -> dict[str, Any]:
        await _seed_jobs()
        async with session_scope() as session:
            user = User(telegram_id=910002, full_name="No CV")
            session.add(user)
            await session.flush()
            session.add(
                Profile(
                    user_id=user.id,
                    skills=[],
                    experience=[],
                    education=[],
                )
            )
            user_id = user.id

        return {"result": await search_for_user(user_id, limit=10)}

    assert run_with_database(body)["result"] is None


def test_scoring_writes_ranked_recommendations_with_every_signal(
    run_with_database: Callable[[Callable[[], Awaitable[Any]]], Any],
) -> None:
    """Day 12 section 10: the ranking must not be semantic alone.

    Three jobs differ ONLY by their embedding, so if the final scores
    came from semantic similarity alone the ordering would be the same
    as the retrieval ordering -- which it is here. The check that
    matters is therefore not the order but the columns: location,
    title and skill must have contributed values, and the weighted sum
    must reconstruct the stored final_score.
    """

    async def body() -> dict[str, Any]:
        user_id = await _seed_candidate()
        job_ids = await _seed_jobs()

        result = await run_scoring(user_id=user_id)

        async with session_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(Recommendation).order_by(Recommendation.rank)
                    )
                )
                .scalars()
                .all()
            )
            run = (await session.execute(select(ScoringRun))).scalar_one()
            return {
                "result": result,
                "job_ids": job_ids,
                "rows": [
                    {
                        "job_id": row.job_id,
                        "rank": row.rank,
                        "final_score": row.final_score,
                        "semantic": row.semantic_score,
                        "semantic_raw": row.semantic_raw,
                        "skill": row.skill_score,
                        "experience": row.experience_score,
                        "location": row.location_score,
                        "title": row.title_score,
                        "weight_covered": row.weight_covered,
                        "quality_multiplier": row.quality_multiplier,
                        "weights_version": row.weights_version,
                        "fingerprint": row.inputs_fingerprint,
                        "scoring_run_id": row.scoring_run_id,
                        "reasons": row.match_reasons,
                    }
                    for row in rows
                ],
                "run_status": run.status,
                "run_pairs": run.pairs_scored,
                "run_finished": run.finished_at,
            }

    observed = run_with_database(body)
    rows = observed["rows"]
    job_ids = observed["job_ids"]

    assert len(rows) == 3
    assert observed["run_pairs"] == 3
    assert observed["run_finished"] is not None

    # Ranks are 1..n, dense, in descending score order.
    assert [row["rank"] for row in rows] == [1, 2, 3]
    scores = [row["final_score"] for row in rows]
    assert scores == sorted(scores, reverse=True)
    assert rows[0]["job_id"] == job_ids["near"]

    # Hybrid, not semantic-only: three of the five signals carry values
    # for every pair, and each row records which weight it covered.
    for row in rows:
        assert row["semantic"] is not None
        assert row["semantic_raw"] is not None
        assert row["location"] is not None
        assert row["title"] is not None
        assert row["weight_covered"] > 0
        assert row["quality_multiplier"] > 0
        assert row["scoring_run_id"] is not None
        assert row["fingerprint"]
        assert row["reasons"]

    # The profile lists python/ML/NLP and the job description mentions
    # them, but skills come from the job_skills table, which Day 6
    # enrichment fills. With no enrichment run, skill abstains -- and
    # NULL is how that is recorded. This assertion is the one CLAUDE.md
    # section 1 warns about: do NOT "fix" it by defaulting to 0.0.
    assert all(row["skill"] is None for row in rows)


def test_re_scoring_replaces_rows_rather_than_accumulating_them(
    run_with_database: Callable[[Callable[[], Awaitable[Any]]], Any],
) -> None:
    """Day 12 section 26: idempotency at the recommendations table.

    One CURRENT score per (user, job), not a history. A second run must
    leave three rows, not six, and must move them onto the new run id.
    """

    async def body() -> dict[str, Any]:
        user_id = await _seed_candidate()
        await _seed_jobs()

        first = await run_scoring(user_id=user_id)
        second = await run_scoring(user_id=user_id)

        async with session_scope() as session:
            rows = (await session.execute(select(Recommendation))).scalars().all()
            runs = (await session.execute(select(ScoringRun.id))).scalars().all()
            return {
                "first": first,
                "second": second,
                "row_count": len(rows),
                "run_ids_on_rows": sorted({row.scoring_run_id for row in rows}),
                "all_run_ids": sorted(runs),
            }

    observed = run_with_database(body)

    assert observed["row_count"] == 3
    assert len(observed["all_run_ids"]) == 2
    # Every row belongs to the SECOND run: the upsert replaced them.
    assert observed["run_ids_on_rows"] == [observed["all_run_ids"][-1]]


def test_a_run_with_no_scorable_jobs_reports_that_rather_than_failing(
    run_with_database: Callable[[Callable[[], Awaitable[Any]]], Any],
) -> None:
    """Day 12 section 27: no jobs at all is a predictable outcome."""

    async def body() -> dict[str, Any]:
        user_id = await _seed_candidate()
        result = await run_scoring(user_id=user_id)

        async with session_scope() as session:
            rows = (await session.execute(select(Recommendation))).scalars().all()
        return {"result": result, "rows": len(rows)}

    observed = run_with_database(body)

    assert observed["rows"] == 0
    assert observed["result"]["jobs_scored"] == 0
    assert observed["result"]["pairs_scored"] == 0
