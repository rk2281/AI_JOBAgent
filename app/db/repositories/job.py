"""Reads and writes for job postings and ingestion bookkeeping.

The only place SQL touching jobs, ingestion_runs or ingestion_rejects
is written. The service decides what a duplicate is and when a job is
stale; this file only executes those decisions.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.ingestion import IngestionReject, IngestionRun, IngestionStatus
from app.db.models.job import Job


class JobRepository:
    """All database access concerning a Job row."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def by_source_and_external_id(
        self,
        source: str,
        external_id: str,
    ) -> Job | None:
        """The first identity check: the same posting from the same source."""
        result = await self._session.execute(
            select(Job).where(
                Job.source == source,
                Job.external_id == external_id,
            )
        )
        return result.scalar_one_or_none()

    async def by_content_hash(self, content_hash: str) -> Job | None:
        """The second identity check: the same job under a different ID.

        Aggregators reissue IDs on repost and carry the same role from
        several boards, so external_id alone under-counts duplicates.
        """
        result = await self._session.execute(
            select(Job).where(Job.content_hash == content_hash).limit(1)
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        source: str,
        external_id: str,
        title: str,
        company: str | None,
        location: str | None,
        description: str | None,
        url: str,
        posted_at: datetime | None,
        content_hash: str,
        is_remote: bool,
        seen_at: datetime,
    ) -> Job:
        job = Job(
            source=source,
            external_id=external_id,
            title=title,
            company=company,
            location=location,
            description=description,
            url=url,
            posted_at=posted_at,
            content_hash=content_hash,
            is_remote=is_remote,
            last_seen_at=seen_at,
            is_active=True,
        )
        self._session.add(job)
        await self._session.flush()
        return job

    async def mark_seen(self, job_id: int, seen_at: datetime) -> None:
        """Refresh a job that turned up again.

        Sets is_active back to True as well as touching last_seen_at.
        A job that was retired for going unseen and has now reappeared
        is open again, and leaving it inactive would hide a live
        vacancy behind a decision made about an older silence.
        """
        await self._session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(last_seen_at=seen_at, is_active=True)
        )

    async def retire_unseen_since(
        self,
        *,
        source: str,
        cutoff: datetime,
    ) -> int:
        """Mark active jobs unseen since `cutoff` as inactive. Returns the count.

        Rows are never deleted. A retired job keeps its history and,
        after Day 7, its embedding -- so a posting that reappears
        costs nothing to revive, where a deleted one would have to be
        ingested and embedded again.
        """
        result = await self._session.execute(
            update(Job)
            .where(
                Job.source == source,
                Job.is_active.is_(True),
                Job.last_seen_at.is_not(None),
                Job.last_seen_at < cutoff,
            )
            .values(is_active=False)
        )
        return result.rowcount or 0

    async def count_active(self, source: str) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(Job)
            .where(Job.source == source, Job.is_active.is_(True))
        )
        return int(result.scalar_one())

    async def count_all(self, source: str) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(Job).where(Job.source == source)
        )
        return int(result.scalar_one())


class IngestionRunRepository:
    """All database access concerning ingestion bookkeeping."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def start(self, source: str, started_at: datetime) -> IngestionRun:
        run = IngestionRun(
            source=source,
            status=IngestionStatus.RUNNING.value,
            started_at=started_at,
        )
        self._session.add(run)
        await self._session.flush()
        return run

    async def finish(
        self,
        run_id: int,
        *,
        status: IngestionStatus,
        finished_at: datetime,
        counters: dict[str, int],
        error_message: str | None = None,
    ) -> None:
        """Write the terminal state and the whole funnel in one statement.

        `counters` is passed as a dict rather than eleven keyword
        arguments so that the service, which owns the funnel, stays the
        only place that knows its shape. Adding a counter later touches
        the service and the model, not this signature.
        """
        await self._session.execute(
            update(IngestionRun)
            .where(IngestionRun.id == run_id)
            .values(
                status=status.value,
                finished_at=finished_at,
                error_message=error_message,
                **counters,
            )
        )

    async def add_reject(
        self,
        *,
        run_id: int,
        source: str,
        external_id: str | None,
        stage: str,
        reason: str,
        raw_payload: dict[str, Any],
        created_at: datetime,
    ) -> None:
        self._session.add(
            IngestionReject(
                run_id=run_id,
                source=source,
                external_id=external_id,
                stage=stage,
                reason=reason,
                raw_payload=raw_payload,
                created_at=created_at,
            )
        )

    async def successful_runs_since(self, source: str, since: datetime) -> int:
        """How many runs actually reached the source since `since`.

        The safety interlock behind retirement. See
        JobIngestionService._retire_stale_jobs.
        """
        result = await self._session.execute(
            select(func.count())
            .select_from(IngestionRun)
            .where(
                IngestionRun.source == source,
                IngestionRun.finished_at.is_not(None),
                IngestionRun.finished_at >= since,
                IngestionRun.status.in_(
                    (
                        IngestionStatus.COMPLETE.value,
                        IngestionStatus.NO_RESULTS.value,
                        IngestionStatus.ALL_REJECTED.value,
                    )
                ),
            )
        )
        return int(result.scalar_one())

    async def latest(self, source: str) -> IngestionRun | None:
        result = await self._session.execute(
            select(IngestionRun)
            .where(IngestionRun.source == source)
            .order_by(IngestionRun.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
