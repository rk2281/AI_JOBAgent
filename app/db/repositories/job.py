"""Reads and writes for job postings and ingestion bookkeeping.

The only place SQL touching jobs, ingestion_runs or ingestion_rejects
is written. The service decides what a duplicate is and when a job is
stale; this file only executes those decisions.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.embedding import EmbeddingRun, EmbeddingStatus
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

    async def by_id(self, job_id: int) -> Job | None:
        """Load one job by primary key, with no other filtering.

        No `is_active` or `embedding IS NOT NULL` clause. self_check()
        uses this to look at a specific row and explain why it cannot
        be searched -- inactive, unembedded, or missing entirely -- and
        a by_id that silently returned None for an inactive job would
        hide which of those it was.
        """
        result = await self._session.execute(select(Job).where(Job.id == job_id))
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

    # --- embeddings (Day 7) -------------------------------------------------
    #
    # Every method below restricts itself to `is_active = True`, and
    # that scope has to be identical across all of them or the numbers
    # stop agreeing. If candidates were selected from active rows but
    # the leftover count were taken over every row, `remaining_null`
    # could never reach 0 and a healthy run would look permanently
    # broken.
    #
    # Inactive rows are skipped on purpose. `is_active = False` means
    # unseen for 21 days, which is a guess about closure rather than a
    # fact -- but embedding them spends quota on rows no Day 8 query
    # will ask for. If that guess is later found wrong, the fix is to
    # re-run this pass, not to have paid for them up front.
    #
    # Named count_active_jobs rather than count_active: the class
    # already has a Day 6 count_active(source) counting rows per
    # ingestion source, and Python has no overloading -- defining a
    # second count_active here would silently replace that method
    # rather than add to it.

    async def count_active_jobs(self) -> int:
        """Active rows, embedded or not. The denominator."""
        result = await self._session.execute(
            select(func.count()).select_from(Job).where(Job.is_active.is_(True))
        )
        return int(result.scalar_one())

    async def count_active_missing_embedding(self) -> int:
        """Active rows with no vector.

        The single most important number Day 7 produces. A row with a
        NULL embedding is not merely unhelpful to Day 8 -- it is
        absent, because `ORDER BY embedding <=> :q` never returns a
        NULL row. Nothing raises, nothing logs, the job simply stops
        existing as far as matching is concerned.
        """
        result = await self._session.execute(
            select(func.count())
            .select_from(Job)
            .where(Job.is_active.is_(True), Job.embedding.is_(None))
        )
        return int(result.scalar_one())

    async def list_needing_embedding(
        self,
        limit: int,
        retry_failed: bool = False,
    ) -> list[Job]:
        """Active rows with no vector yet.

        By default this excludes rows that have already been attempted
        and failed. Without that filter a permanently broken row --
        one whose text the provider will reject every time -- would be
        retried on every run forever, spending quota to fail
        identically. `embedding_attempts` is what makes "not tried
        yet" and "tried and failed" separable, and this is what that
        separation buys.

        `--retry-failed` sets retry_failed=True for the case where the
        failure was the provider's rather than the row's.
        """
        query = select(Job).where(
            Job.is_active.is_(True),
            Job.embedding.is_(None),
        )
        if not retry_failed:
            query = query.where(Job.embedding_attempts == 0)

        # Oldest first, so an interrupted run resumes predictably
        # rather than reshuffling which rows are still missing.
        result = await self._session.execute(query.order_by(Job.id).limit(limit))
        return list(result.scalars().all())

    async def list_active_for_recheck(self, limit: int) -> list[Job]:
        """Every active row, embedded or not, for staleness comparison.

        Staleness cannot be expressed in SQL. Whether a stored vector
        is current depends on whether `embedding_source_hash` matches
        what `build_job_document()` produces from the row TODAY, and
        that function lives in Python. So this returns the rows and
        the service does the comparing.
        """
        result = await self._session.execute(
            select(Job).where(Job.is_active.is_(True)).order_by(Job.id).limit(limit)
        )
        return list(result.scalars().all())

    async def set_embedding(
        self,
        job_id: int,
        vector: list[float],
        model: str,
        source_hash: str,
        embedded_at: datetime,
    ) -> None:
        """Store a vector and everything needed to judge it later.

        `embedding_error` is cleared on success. A row that failed
        once and then succeeded must not keep a stale error message,
        or a later reader will conclude the current vector is
        suspect.
        """
        await self._session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                embedding=vector,
                embedding_model=model,
                embedding_source_hash=source_hash,
                embedded_at=embedded_at,
                embedding_error=None,
                embedding_attempts=Job.embedding_attempts + 1,
            )
        )

    async def mark_embedding_failed(self, job_id: int, error: str) -> None:
        """Record a failed attempt without touching the vector.

        The vector is left exactly as it was on purpose. A re-embed
        that fails must not destroy a working older vector -- a stale
        embedding still returns the row from a search, and a NULL one
        makes it vanish. Worse is worse.
        """
        await self._session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                embedding_error=error,
                embedding_attempts=Job.embedding_attempts + 1,
            )
        )

    # --- similarity search (Day 7) ------------------------------------------

    async def nearest_to(
        self,
        vector: list[float],
        limit: int = 10,
        ef_search: int | None = None,
    ) -> list[tuple[Job, float]]:
        """The `limit` closest active jobs to `vector`, nearest first.

        Uses cosine_distance(), which emits the `<=>` operator. That
        is not cosmetic: the HNSW indexes were built with
        vector_cosine_ops, and `<->` or `<#>` would silently skip the
        index AND return different neighbours, with no error either
        way.

        `embedding IS NOT NULL` is stated explicitly even though a
        NULL row could never win an ordering. Writing it down makes
        the exclusion visible to whoever reads this next, because the
        cost of that exclusion is invisible everywhere else: a job
        with no vector is not ranked low, it is absent.
        """
        if ef_search is not None:
            # SET LOCAL, so it reverts when this transaction ends
            # rather than leaking into whatever the session does next.
            # The pgvector default is 40; a limit at or above that
            # starts costing recall, because the index only keeps 40
            # candidates in flight to choose from.
            await self._session.execute(
                text(f"SET LOCAL hnsw.ef_search = {int(ef_search)}")
            )

        distance = Job.embedding.cosine_distance(vector)

        result = await self._session.execute(
            select(Job, distance.label("distance"))
            .where(Job.is_active.is_(True), Job.embedding.isnot(None))
            .order_by(distance)
            .limit(limit)
        )
        return [(row[0], float(row[1])) for row in result.all()]

    async def explain_nearest(
        self,
        vector: list[float],
        limit: int = 10,
        disable_seqscan: bool = False,
    ) -> list[str]:
        """The query plan for nearest_to(), as printable lines.

        `disable_seqscan` exists because of the size of this table. At
        99 rows the planner will pick a sequential scan and be right to
        -- reading 99 rows costs less than consulting an index. So the
        useful question is not "is the index used" but "CAN it be",
        and turning seqscan off for one transaction answers it. If the
        plan then names the HNSW index, the operator and opclass agree
        and the query shape is indexable. If it still scans, something
        is genuinely wrong.
        """
        if disable_seqscan:
            await self._session.execute(text("SET LOCAL enable_seqscan = off"))

        distance = Job.embedding.cosine_distance(vector)

        query = (
            select(Job.id, distance.label("distance"))
            .where(Job.is_active.is_(True), Job.embedding.isnot(None))
            .order_by(distance)
            .limit(limit)
        )

        compiled = query.compile(
            dialect=self._session.bind.dialect,
            compile_kwargs={"literal_binds": True},
        )

        result = await self._session.execute(
            text(f"EXPLAIN ANALYZE {compiled}")
        )
        return [row[0] for row in result.all()]


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


class EmbeddingRunRepository:
    """All database access concerning an EmbeddingRun row."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def start(self, scope: str, model: str, started_at: datetime) -> EmbeddingRun:
        """Open a run row before any work happens.

        Written first, and deliberately, so that a pass killed
        mid-flight leaves a row stuck at `running` rather than no row
        at all. A crash that erases its own evidence is the hardest
        kind to investigate.
        """
        run = EmbeddingRun(
            scope=scope,
            status=EmbeddingStatus.RUNNING.value,
            model=model,
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
        counters: dict[str, int],
        remaining_null: int,
        error_message: str | None = None,
    ) -> None:
        await self._session.execute(
            update(EmbeddingRun)
            .where(EmbeddingRun.id == run_id)
            .values(
                status=status,
                finished_at=finished_at,
                remaining_null=remaining_null,
                error_message=error_message,
                **counters,
            )
        )
