"""Reads and writes for job postings and ingestion bookkeeping.

The only place SQL touching jobs, ingestion_runs or ingestion_rejects
is written. The service decides what a duplicate is and when a job is
stale; this file only executes those decisions.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.embedding import EmbeddingRun, EmbeddingStatus
from app.db.models.ingestion import IngestionReject, IngestionRun, IngestionStatus
from app.db.models.job import Job, JobSkill
from app.db.models.skill import Skill


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

    # --- job enrichment (Day 8) ---------------------------------------------
    #
    # Same `is_active = True` scoping rule as the embedding methods
    # above, and for the same reason: if candidates were selected
    # from active rows but the leftover count were taken over every
    # row, `remaining` could never reach 0 and a healthy run would
    # look permanently broken.
    #
    # `is_excluded` is deliberately NOT filtered here. An excluded
    # job is skipped by SCORING, which is a different question from
    # whether it has skills. Filtering it at this layer would make
    # the enrichment counts disagree with the job counts for a
    # reason nothing states.

    async def count_active_missing_skills(self) -> int:
        """Active rows with no job_skills and no attempt recorded.

        The enrichment equivalent of count_active_missing_embedding,
        with one important difference. A NULL embedding makes a row
        INVISIBLE to every similarity query. Missing skills do not:
        the row still ranks, it just abstains on the 30% signal. So
        this number is not "rows that vanished", it is "rows scoring
        on 70% of the model", which is worse in a subtler way --
        those jobs are still ranked, and ranked against jobs that had
        the full 100%.
        """
        # Uses NOT EXISTS against job_skills rather than an outer
        # join with a NULL test. Both are correct; NOT EXISTS says
        # what is meant and stops at the first matching row.
        has_skills = select(JobSkill.job_id).where(JobSkill.job_id == Job.id).exists()

        result = await self._session.execute(
            select(func.count())
            .select_from(Job)
            .where(
                Job.is_active.is_(True),
                Job.skills_extraction_attempts == 0,
                ~has_skills,
            )
        )
        return int(result.scalar_one())

    async def list_needing_enrichment(
        self,
        limit: int,
        retry_failed: bool = False,
    ) -> list[Job]:
        """Active rows that have no skills and are still worth trying.

        Eligibility is: active, no job_skills rows, and
        skills_extraction_attempts < settings.enrichment_max_attempts.

        That attempts test is the one thing here that deliberately
        differs from the embedding pass, which filters on
        `attempts == 0`. That was right there because those failures
        were deterministic -- a dimension mismatch fails identically
        every time, so one attempt is all the information available.

        Here the failure is variance. Fifteen timed calls during
        isolation ran from 7.4s to 74.1s for requests that never
        changed, against a 90s ceiling. A row that times out is very
        often merely unlucky, and a binary filter would drop it from
        every future run permanently for being slow once.

        `retry_failed=True` ignores the attempts ceiling entirely,
        for the case where the failure was the provider's rather than
        the row's.

        Ordered by id, oldest first, so an interrupted run resumes
        predictably rather than reshuffling which rows are left.
        """
        has_skills = select(JobSkill.job_id).where(JobSkill.job_id == Job.id).exists()

        query = select(Job).where(Job.is_active.is_(True), ~has_skills)
        if not retry_failed:
            query = query.where(
                Job.skills_extraction_attempts < settings.enrichment_max_attempts
            )

        result = await self._session.execute(query.order_by(Job.id).limit(limit))
        return list(result.scalars().all())

    async def set_enrichment(
        self,
        job_id: int,
        *,
        model: str,
        source_hash: str,
        min_experience_years: int | None,
        max_experience_years: int | None,
        work_mode: str | None,
        extracted_at: datetime,
    ) -> None:
        """Record a successful extraction on the jobs row.

        Does NOT write job_skills -- replace_job_skills does that,
        because it needs the skills catalog and this method must not
        reach into a second repository.

        `skills_extraction_error` is cleared on success, for the same
        reason set_embedding clears embedding_error: a row that
        failed once and then succeeded must not keep a stale message,
        or a later reader concludes the current data is suspect.

        min_experience_years and max_experience_years are written
        even when both are None. That is not a no-op: None here means
        the posting was SILENT about experience, which is what the
        experience signal must abstain on, and it is different from 0,
        which means the posting explicitly welcomes freshers. Writing
        them explicitly keeps that distinction in the column rather
        than in an assumption about what an unwritten column means.
        """
        await self._session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                skills_extraction_model=model,
                skills_source_hash=source_hash,
                min_experience_years=min_experience_years,
                max_experience_years=max_experience_years,
                work_mode=work_mode,
                skills_extracted_at=extracted_at,
                skills_extraction_error=None,
                skills_extraction_attempts=Job.skills_extraction_attempts + 1,
            )
        )

    async def mark_enrichment_failed(self, job_id: int, error: str) -> None:
        """Record a failed attempt without touching anything else.

        Existing job_skills rows are left alone, exactly as
        mark_embedding_failed leaves a stale vector alone: a re-run
        that fails must not destroy working older data. Stale skills
        still let a job score on 100% of the model; no skills makes
        it abstain on 30%.

        `error` must come from a describe_*_error() helper. Never
        str(exc) -- a google-genai error can echo back the request,
        and on this path the request is job text.
        """
        await self._session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                skills_extraction_error=error,
                skills_extraction_attempts=Job.skills_extraction_attempts + 1,
            )
        )

    async def replace_job_skills(
        self,
        job_id: int,
        skill_ids: list[int],
    ) -> int:
        """Set a job's skills to exactly this set. Returns rows written.

        Delete-then-insert rather than an upsert, because this is a
        REPLACEMENT: a re-run that finds fewer skills must remove the
        ones no longer found, and an upsert would only ever grow the
        set. A job whose skills only accumulate would drift towards a
        larger denominator over time, quietly lowering every
        candidate's skill score.

        Takes skill_ids, not names. Resolving a name to a catalog row
        is SkillRepository.get_or_create's job, and doing it here
        would put two repositories' SQL in one method.

        Returns the number of rows inserted so the caller can compare
        it against the number of skills it asked for. A silent
        difference between those two is exactly the kind of thing
        that shows up later as scores that feel slightly wrong.
        """
        await self._session.execute(delete(JobSkill).where(JobSkill.job_id == job_id))

        if not skill_ids:
            return 0

        self._session.add_all(
            [JobSkill(job_id=job_id, skill_id=skill_id) for skill_id in skill_ids]
        )
        await self._session.flush()
        return len(skill_ids)

    # --- scoring (Day 8) -----------------------------------------------------
    #
    # "Scorable" means three things at once: active, embedded, and not
    # excluded. Each of the three exclusions it replaces means something
    # different, and the scoring run counts them separately rather than
    # folding them into one "skipped" number. An inactive job is a guess
    # about closure. A job with no embedding is INVISIBLE to similarity
    # search -- not ranked low, absent, because `ORDER BY embedding <=> :q`
    # never returns a NULL row. An excluded job is a human decision, made
    # by hand about one specific posting. Collapsing the three would hide
    # which one actually happened when a job goes missing from scoring.

    async def count_scorable_jobs(self) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(Job)
            .where(
                Job.is_active.is_(True),
                Job.embedding.isnot(None),
                Job.is_excluded.is_(False),
            )
        )
        return int(result.scalar_one())

    async def list_scorable_jobs(self) -> list[Job]:
        """Every job eligible to be scored against a candidate.

        See the class comment above for why active/embedded/not-excluded
        are three separate conditions rather than one. Ordered by id so
        two runs over the same data enumerate jobs the same way.
        """
        result = await self._session.execute(
            select(Job)
            .where(
                Job.is_active.is_(True),
                Job.embedding.isnot(None),
                Job.is_excluded.is_(False),
            )
            .order_by(Job.id)
        )
        return list(result.scalars().all())

    async def skills_for_jobs(self, job_ids: list[int]) -> dict[int, list[str]]:
        """Normalized skill keys for a batch of jobs, one query.

        Every requested id is a key in the result, even a job with zero
        job_skills rows -- it gets an empty list, not a missing key. A
        missing key and an empty list would be indistinguishable at the
        call site, and the scorer needs "no skills" to actually reach
        score_skill() so it can abstain there; a job silently absent
        from this dict would abstain for a reason nothing records.

        Returns skills.normalized_name, not skills.name, because that
        is the matching surface profiles.skills is written in.
        """
        result: dict[int, list[str]] = {job_id: [] for job_id in job_ids}
        if not job_ids:
            return result

        rows = await self._session.execute(
            select(JobSkill.job_id, Skill.normalized_name)
            .join(Skill, Skill.id == JobSkill.skill_id)
            .where(JobSkill.job_id.in_(job_ids))
        )
        for job_id, normalized_name in rows.all():
            result[job_id].append(normalized_name)

        return result

    async def count_active_missing_embedding_scorable(self) -> int:
        """Active, not excluded, still unembedded.

        The number that explains how many otherwise-eligible jobs could
        not be scored at all this run, as distinct from jobs held back
        by exclusion. Feeds scoring_runs.jobs_skipped_no_embedding.
        """
        result = await self._session.execute(
            select(func.count())
            .select_from(Job)
            .where(
                Job.is_active.is_(True),
                Job.embedding.is_(None),
                Job.is_excluded.is_(False),
            )
        )
        return int(result.scalar_one())

    async def count_active_embedded_jobs(self) -> int:
        """The candidate pool nearest_to() actually searches.

        Active and embedded, INCLUDING excluded rows -- deliberately
        the one Day 8 count that ignores `is_excluded`, because it
        exists to match nearest_to()'s own WHERE clause exactly. Any
        divergence between the two is the bug this method was added
        to close: a limit drawn from a smaller set than the query
        selects from silently truncates the far end of the result.

        Counted independently rather than derived as
        count_active_jobs() - count_active_missing_embedding(). Those
        two subtract to the same value today, and a subtraction can
        never disagree with itself -- it would make the funnel
        assertion an equation solved to be true rather than a check.
        Two queries that can disagree are the point.
        """
        result = await self._session.execute(
            select(func.count())
            .select_from(Job)
            .where(
                Job.is_active.is_(True),
                Job.embedding.isnot(None),
            )
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
