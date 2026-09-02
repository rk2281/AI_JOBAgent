"""Reads and writes for scoring runs and their recommendation rows.

Two repositories, mirroring the embedding pass's split between
EmbeddingRunRepository (bookkeeping) and JobRepository (the rows the
pass actually writes): ScoringRunRepository owns scoring_runs,
RecommendationRepository owns recommendations. Neither reaches into
the other's table.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.recommendation import Recommendation
from app.db.models.scoring import ScoringRun, ScoringStatus


class ScoringRunRepository:
    """All database access concerning a ScoringRun row."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def start(self, weights_version: int, started_at: datetime) -> ScoringRun:
        """Open a run row before any work happens.

        Written first, and deliberately, so that a pass killed
        mid-flight leaves a row stuck at 'running' rather than no row
        at all. A crash that erases its own evidence is the hardest
        kind to investigate.
        """
        run = ScoringRun(
            status=ScoringStatus.RUNNING.value,
            weights_version=weights_version,
            started_at=started_at,
        )
        self._session.add(run)
        await self._session.flush()
        return run

    async def finish(
        self,
        run_id: int,
        status: str,
        finished_at: datetime,
        counters: dict,
        error_message: str | None = None,
    ) -> None:
        """Write the terminal state and the whole funnel in one statement.

        `counters` is passed as a dict rather than two dozen keyword
        arguments, exactly like IngestionRunRepository.finish, so the
        service -- which owns the funnel's shape -- stays the only
        place that knows it. Adding a counter later touches the
        service and the model, not this signature.
        """
        await self._session.execute(
            update(ScoringRun)
            .where(ScoringRun.id == run_id)
            .values(
                status=status,
                finished_at=finished_at,
                error_message=error_message,
                **counters,
            )
        )

    async def latest(self) -> ScoringRun | None:
        result = await self._session.execute(
            select(ScoringRun).order_by(ScoringRun.id.desc()).limit(1)
        )
        return result.scalar_one_or_none()


class RecommendationRepository:
    """All database access concerning a Recommendation row."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self,
        *,
        user_id: int,
        job_id: int,
        scoring_run_id: int,
        semantic_score: float | None,
        skill_score: float | None,
        experience_score: float | None,
        location_score: float | None,
        title_score: float | None,
        semantic_raw: float | None,
        weight_covered: float,
        quality_multiplier: float,
        weights_version: int,
        inputs_fingerprint: str | None,
        final_score: float,
        rank: int | None,
        match_reasons: list[str],
    ) -> None:
        """INSERT ... ON CONFLICT (user_id, job_id) DO UPDATE.

        An upsert, not a plain insert, because the unique constraint
        on (user_id, job_id) is deliberate: one CURRENT score per
        pair, not a history. Re-scoring the same pair must replace
        the row, not add a second one competing with it.

        All five signal columns are written on every call, even when
        the value is None. NULL is the abstain marker on this table,
        and leaving a column out of the UPDATE would let it keep
        whatever a PREVIOUS run wrote there -- a stale skill_score
        surviving under a new run's scoring_run_id, looking exactly
        like it was computed just now.
        """
        statement = insert(Recommendation).values(
            user_id=user_id,
            job_id=job_id,
            scoring_run_id=scoring_run_id,
            semantic_score=semantic_score,
            skill_score=skill_score,
            experience_score=experience_score,
            location_score=location_score,
            title_score=title_score,
            semantic_raw=semantic_raw,
            weight_covered=weight_covered,
            quality_multiplier=quality_multiplier,
            weights_version=weights_version,
            inputs_fingerprint=inputs_fingerprint,
            final_score=final_score,
            rank=rank,
            match_reasons=match_reasons,
        )

        statement = statement.on_conflict_do_update(
            index_elements=["user_id", "job_id"],
            set_={
                "scoring_run_id": statement.excluded.scoring_run_id,
                "semantic_score": statement.excluded.semantic_score,
                "skill_score": statement.excluded.skill_score,
                "experience_score": statement.excluded.experience_score,
                "location_score": statement.excluded.location_score,
                "title_score": statement.excluded.title_score,
                "semantic_raw": statement.excluded.semantic_raw,
                "weight_covered": statement.excluded.weight_covered,
                "quality_multiplier": statement.excluded.quality_multiplier,
                "weights_version": statement.excluded.weights_version,
                "inputs_fingerprint": statement.excluded.inputs_fingerprint,
                "final_score": statement.excluded.final_score,
                "rank": statement.excluded.rank,
                "match_reasons": statement.excluded.match_reasons,
            },
        )

        await self._session.execute(statement)

    async def top_for_user(self, user_id: int, limit: int) -> list[Recommendation]:
        """The user's `limit` highest-scoring current rows.

        No scoring_run_id filter. There is at most one row per
        (user_id, job_id) by construction -- upsert() replaces rather
        than adds -- so "current" and "from the latest run" are the
        same set for any job the latest run actually touched, and a
        job the latest run skipped simply keeps its last known score
        rather than vanishing from the table.
        """
        result = await self._session.execute(
            select(Recommendation)
            .where(Recommendation.user_id == user_id)
            .order_by(Recommendation.final_score.desc(), Recommendation.job_id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def bottom_for_user(self, user_id: int, limit: int) -> list[Recommendation]:
        result = await self._session.execute(
            select(Recommendation)
            .where(Recommendation.user_id == user_id)
            .order_by(Recommendation.final_score.asc(), Recommendation.job_id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def distinct_score_count(self, run_id: int) -> int:
        """How many distinct final_score values one run produced.

        Scoped to `scoring_run_id = run_id`, unlike top/bottom_for_user
        above -- this answers a question about one specific run's
        output, not about the table's current state.
        """
        result = await self._session.execute(
            select(func.count(func.distinct(Recommendation.final_score))).where(
                Recommendation.scoring_run_id == run_id
            )
        )
        return int(result.scalar_one())

    async def score_stats(
        self, run_id: int
    ) -> tuple[float | None, float | None, float | None]:
        """(min, max, median) of final_score for one run.

        percentile_cont(0.5) rather than a manual sort-and-index in
        Python, because the run can cover every user and every
        scorable job, and Postgres already has to read every row in
        this WHERE clause regardless -- there is no reason to also
        pull them all into the application to find the middle one.
        """
        result = await self._session.execute(
            select(
                func.min(Recommendation.final_score),
                func.max(Recommendation.final_score),
                func.percentile_cont(0.5).within_group(Recommendation.final_score),
            ).where(Recommendation.scoring_run_id == run_id)
        )
        row = result.one()
        return row[0], row[1], row[2]
