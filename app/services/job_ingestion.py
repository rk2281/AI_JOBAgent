"""Fetching job postings, deciding which to keep, and recording what happened.

run_ingestion() is the only function outside this module anything
should call. It returns without raising for any expected failure --
the source being down, the quota being spent, the provider changing
its schema -- and the outcome lives in the ingestion_runs row and in
the returned IngestionResult, so a caller does not have to catch the
right exception type to find out what happened. Same contract as
extract_cv in app.services.cv_extraction, for the same reason.

It knows nothing about Telegram and nothing about Adzuna. Its source
is a JobSource, satisfied today by AdzunaClient and tomorrow by
whatever else; that is what makes the second source a new file in
app.integrations rather than a rewrite of this one.

WHY IT OPENS ITS OWN TRANSACTIONS AND TAKES NO SESSION

Same lesson as extract_cv, learned the same expensive way. Holding a
transaction open across a call to a third party lends a database
backend to someone else's latency, and Neon closes a connection with
IdleInTransactionSessionTimeout while it waits. So the HTTP call
happens with NO session open, and each batch of writes gets its own
short transaction. Do not wrap run_ingestion in session_scope().

WHAT A ZERO MEANS, AND WHY THE COUNTERS ARE A FUNNEL

A run can end with zero new jobs for six different reasons and only
one of them is healthy -- see IngestionStatus for the list. The
counters here are therefore mutually exclusive: every record fetched
leaves through exactly one of normalize_failed, validation_failed,
filtered_out, duplicates or inserted, and the total is asserted at the
end. A record lost between them would otherwise show up as a number
being slightly smaller than expected, which is to say not at all.
"""

from __future__ import annotations

import hashlib
import html
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from app.core.config import settings
from app.db.models.ingestion import IngestionStatus
from app.db.repositories.job import IngestionRunRepository, JobRepository
from app.db.session import session_scope
from app.integrations.adzuna import (
    JobSourceError,
    JobSourceQuotaError,
    SearchPage,
)
from app.schemas.job import RawJobPosting
from app.services.locations import normalize_location

logger = logging.getLogger(__name__)

_TAG_PATTERN = re.compile(r"<[^>]+>")
_WHITESPACE_PATTERN = re.compile(r"\s+")

# Adzuna truncates every description to exactly this, ending in an
# ellipsis -- measured across 27 postings from three queries, all
# identical. Stripping the marker keeps it out of the text Day 7
# embeds, where it is noise repeated on every single job and therefore
# carries no signal while consuming tokens.
_TRUNCATION_MARKERS = ("…", "...")


class JobSource(Protocol):
    """What run_ingestion needs from a source. Deliberately tiny.

    A Protocol rather than a base class so a source does not have to
    import anything from here to satisfy it, and so a test fake is an
    ordinary object rather than a subclass. The narrower this is, the
    less a second source has to reproduce.
    """

    @property
    def source_name(self) -> str: ...

    async def search(
        self,
        *,
        what: str = "",
        where: str = "",
        page: int = 1,
        max_days_old: int | None = None,
        results_per_page: int | None = None,
    ) -> SearchPage: ...


@dataclass
class _Counters:
    """The funnel. Every fetched record leaves through exactly one field."""

    queries_attempted: int = 0
    pages_fetched: int = 0
    records_fetched: int = 0
    normalize_failed: int = 0
    validation_failed: int = 0
    filtered_out: int = 0
    duplicates: int = 0
    inserted: int = 0
    retired: int = 0

    def accounted_for(self) -> int:
        return (
            self.normalize_failed
            + self.validation_failed
            + self.filtered_out
            + self.duplicates
            + self.inserted
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "queries_attempted": self.queries_attempted,
            "pages_fetched": self.pages_fetched,
            "records_fetched": self.records_fetched,
            "normalize_failed": self.normalize_failed,
            "validation_failed": self.validation_failed,
            "filtered_out": self.filtered_out,
            "duplicates": self.duplicates,
            "inserted": self.inserted,
            "retired": self.retired,
        }


@dataclass(frozen=True)
class IngestionResult:
    """What happened, handed back synchronously.

    The ingestion_runs row is the durable record; this is the same
    answer returned directly so a caller is not made to re-query for
    something it was just told. Same shape as ExtractionResult.
    """

    run_id: int | None
    status: IngestionStatus
    counters: dict[str, int]
    error: str | None = None

    @property
    def is_healthy(self) -> bool:
        """Whether this run did what it was supposed to.

        NO_RESULTS is excluded deliberately. One empty run is normal;
        the point of it having its own status is that a sequence of
        them is a symptom, and calling it healthy would hide that.
        """
        return self.status is IngestionStatus.COMPLETE


def clean_description(raw: str | None) -> str | None:
    """Strip markup and the truncation marker from a description.

    Cleaned at ingestion rather than at embedding time because both
    consumers want the same thing: Day 7 embeds this text and Day 11
    shows it to a user, and neither wants entity escapes or a trailing
    ellipsis. Doing it once here means the two cannot drift apart.

    The cost, stated plainly: the provider's original formatting is
    not kept anywhere. That is acceptable for 500-character excerpts
    and would not be for full postings.
    """
    if not raw:
        return None

    text = html.unescape(raw)
    text = _TAG_PATTERN.sub(" ", text)
    text = _WHITESPACE_PATTERN.sub(" ", text).strip()

    for marker in _TRUNCATION_MARKERS:
        if text.endswith(marker):
            text = text[: -len(marker)].strip()

    return text or None


def compute_content_hash(
    title: str,
    company: str | None,
    location: str | None,
) -> str:
    """A stable identity for "the same job" across IDs and boards.

    Title, company and location. Not the URL -- the same posting
    reaches an aggregator through several boards under several URLs,
    and that is the case this exists to catch. Not the description
    either: aggregators rewrite and truncate them, so two records for
    one job would hash differently, which is the opposite of useful.

    Location goes through normalize_location so "Gurgaon" and
    "Gurugram" produce one hash rather than two.

    The limit, stated so it is not discovered later as a bug: this is
    exact matching. "Senior ML Engineer" and "Senior Machine Learning
    Engineer" at the same company will be two rows. Fuzzy title
    matching is a genuinely hard problem and putting it here would
    make dedup unpredictable in exchange for catching a minority of
    cases.
    """
    parts = [
        " ".join(title.strip().lower().split()),
        " ".join((company or "").strip().lower().split()),
        normalize_location(location),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


class JobIngestionService:
    """Turns a source's search results into stored, deduplicated jobs."""

    def __init__(
        self,
        source: JobSource,
        *,
        now: datetime | None = None,
    ) -> None:
        """
        `now` is injectable so freshness and retirement can be tested
        without waiting or patching the clock -- the same reasoning as
        extract_cv accepting stale_after.
        """
        self._source = source
        self._now = now or datetime.now(UTC)

    async def run(
        self,
        *,
        keywords: list[str] | None = None,
        locations: list[str] | None = None,
        max_pages: int | None = None,
    ) -> IngestionResult:
        counters = _Counters()
        keywords = keywords if keywords is not None else settings.adzuna_keyword_list
        locations = locations if locations is not None else settings.adzuna_location_list
        max_pages = max_pages or settings.adzuna_max_pages_per_run

        # Phase 1: open the run, then get out of the database before
        # any network call. Short transaction, committed immediately.
        async with session_scope() as session:
            run = await IngestionRunRepository(session).start(
                self._source.source_name,
                self._now,
            )
            run_id = run.id

        # Phase 2: fetch. NO session open. See the module docstring.
        pages: list[SearchPage] = []
        fatal_status: IngestionStatus | None = None
        fatal_error: str | None = None

        for keyword in keywords:
            for location in locations:
                counters.queries_attempted += 1
                for page_number in range(1, max_pages + 1):
                    try:
                        page = await self._source.search(
                            what=keyword,
                            where=location,
                            page=page_number,
                        )
                    except JobSourceQuotaError as error:
                        # Terminal for the whole run, not just this
                        # query. The quota is per account and monthly;
                        # continuing would spend what is left and
                        # bury the cause under identical failures.
                        fatal_status = IngestionStatus.QUOTA_EXCEEDED
                        fatal_error = str(error)
                        break
                    except JobSourceError as error:
                        fatal_status = IngestionStatus.SOURCE_ERROR
                        fatal_error = str(error)
                        break

                    counters.pages_fetched += 1
                    pages.append(page)

                    # A short page is the last page. Requesting the
                    # next one would spend a call to be told nothing.
                    if len(page.postings) + len(page.unparseable) == 0:
                        break

                if fatal_status is not None:
                    break
            if fatal_status is not None:
                break

        # Phase 3: process and write, in a fresh short transaction.
        async with session_scope() as session:
            job_repo = JobRepository(session)
            run_repo = IngestionRunRepository(session)

            for page in pages:
                counters.records_fetched += page.records_returned

                for bad in page.unparseable:
                    counters.normalize_failed += 1
                    await run_repo.add_reject(
                        run_id=run_id,
                        source=self._source.source_name,
                        external_id=_maybe_external_id(bad.raw),
                        stage="normalize",
                        reason=bad.reason,
                        raw_payload=bad.raw,
                        created_at=self._now,
                    )

                for posting in page.postings:
                    await self._process_posting(
                        posting,
                        counters=counters,
                        job_repo=job_repo,
                        run_repo=run_repo,
                        run_id=run_id,
                    )

            if fatal_status is None:
                counters.retired = await self._retire_stale_jobs(
                    job_repo=job_repo,
                    run_repo=run_repo,
                )

            status = fatal_status or _classify(counters)

            # The funnel must balance. A mismatch means a record left
            # the pipeline without being counted, which is exactly the
            # kind of silent loss this whole design exists to make
            # impossible. Assert rather than log: a wrong number that
            # keeps running is worse than a crash, because it will be
            # trusted.
            assert counters.records_fetched == counters.accounted_for(), (
                f"funnel does not balance: fetched={counters.records_fetched} "
                f"accounted={counters.accounted_for()} {counters.as_dict()}"
            )

            await run_repo.finish(
                run_id,
                status=status,
                finished_at=datetime.now(UTC),
                counters=counters.as_dict(),
                error_message=fatal_error,
            )

        logger.info(
            "Ingestion run %s finished: status=%s fetched=%s inserted=%s "
            "duplicates=%s rejected=%s filtered=%s retired=%s",
            run_id,
            status.value,
            counters.records_fetched,
            counters.inserted,
            counters.duplicates,
            counters.normalize_failed + counters.validation_failed,
            counters.filtered_out,
            counters.retired,
        )

        return IngestionResult(
            run_id=run_id,
            status=status,
            counters=counters.as_dict(),
            error=fatal_error,
        )

    async def _process_posting(
        self,
        posting: RawJobPosting,
        *,
        counters: _Counters,
        job_repo: JobRepository,
        run_repo: IngestionRunRepository,
        run_id: int,
    ) -> None:
        """Validate, filter, then store or recognise one posting."""
        problem = self._validation_problem(posting)
        if problem is not None:
            counters.validation_failed += 1
            await run_repo.add_reject(
                run_id=run_id,
                source=posting.source,
                external_id=posting.external_id or None,
                stage="validate",
                reason=problem,
                raw_payload=posting.model_dump(mode="json"),
                created_at=self._now,
            )
            return

        if self._is_too_old(posting):
            # Counted, not stored. A job outside the freshness window
            # is not junk -- storing thousands of perfectly good jobs
            # we simply did not ask for would cost more than the count
            # is worth, and the count still answers "was anything
            # dropped here".
            counters.filtered_out += 1
            return

        existing = await job_repo.by_source_and_external_id(
            posting.source,
            posting.external_id,
        )

        content_hash = compute_content_hash(
            posting.title,
            posting.company,
            posting.location,
        )

        if existing is None:
            existing = await job_repo.by_content_hash(content_hash)

        if existing is not None:
            # The repost case. Refresh rather than insert: the user is
            # never re-notified about a posting they already
            # dismissed, and the duplicate never reaches Day 7's
            # embedding call or Day 8's scoring, which is where a
            # second row would actually cost something.
            counters.duplicates += 1
            await job_repo.mark_seen(existing.id, self._now)
            return

        await job_repo.create(
            source=posting.source,
            external_id=posting.external_id,
            title=posting.title,
            company=posting.company,
            location=posting.location,
            description=clean_description(posting.description),
            url=posting.url,
            posted_at=posting.posted_at,
            content_hash=content_hash,
            is_remote=posting.is_remote,
            seen_at=self._now,
        )
        counters.inserted += 1

    def _validation_problem(self, posting: RawJobPosting) -> str | None:
        """Return why this posting cannot be stored, or None if it can.

        Returns a reason string rather than a bool so the reject row
        answers "why did this job never appear" specifically enough to
        act on. "validation_failed" three weeks later tells nobody
        whether the filter is wrong or the data is.
        """
        if not posting.url.lower().startswith(("http://", "https://")):
            return f"url does not look like a link: {posting.url[:80]!r}"

        if len(posting.title.strip()) < 2:
            return "title is empty or a single character"

        if posting.posted_at is None:
            # Not fatal in principle, but a job with no date cannot be
            # aged out and would sit active forever. Rejecting it is a
            # choice, and it is recorded here so the choice is
            # visible rather than implicit.
            return "no posting date, so freshness and expiry cannot be applied"

        return None

    def _is_too_old(self, posting: RawJobPosting) -> bool:
        """Re-check freshness locally even though the source was asked for it.

        The source is asked via max_days_old, which is where the real
        saving is -- a job never sent costs no quota and no Day 7
        embedding. This second check is not redundant: it verifies the
        provider honoured the parameter, and it is the only thing that
        would notice if it stopped. A filter trusted without
        verification is a filter that can silently stop working.
        """
        if posting.posted_at is None:
            return False
        cutoff = self._now - timedelta(days=settings.adzuna_max_days_old)
        return posting.posted_at < cutoff

    async def _retire_stale_jobs(
        self,
        *,
        job_repo: JobRepository,
        run_repo: IngestionRunRepository,
    ) -> int:
        """Mark long-unseen jobs inactive, but only when that is meaningful.

        THE INTERLOCK, which is the important part of this method.

        Retirement infers "closed" from "not seen recently". That
        inference is only valid if we have actually been looking. If
        ingestion has not run for a week -- laptop off, quota spent,
        script forgotten -- then nothing has been seen recently, and
        retiring on that basis would mark the entire table inactive in
        one pass without a single vacancy having closed.

        So retirement requires at least one successful run inside the
        recent window. No recent runs means no evidence, and no
        evidence means no conclusion.

        Worth stating what this still cannot do: the source never
        reports a closed vacancy, and our queries are keyword-scoped,
        so a job absent from today's results may simply not have
        matched today's search. Unseen is a proxy for closed. It is
        the best available one, not a correct one.
        """
        interlock_window = timedelta(days=settings.job_retire_requires_run_within_days)
        recent_successes = await run_repo.successful_runs_since(
            self._source.source_name,
            self._now - interlock_window,
        )

        if recent_successes == 0:
            logger.info(
                "Skipping retirement: no successful run in the last %s days, "
                "so 'unseen' carries no information.",
                settings.job_retire_requires_run_within_days,
            )
            return 0

        cutoff = self._now - timedelta(days=settings.job_retire_after_days)
        return await job_repo.retire_unseen_since(
            source=self._source.source_name,
            cutoff=cutoff,
        )


def _classify(counters: _Counters) -> IngestionStatus:
    """Decide which kind of outcome this run had.

    The order of these branches is the whole point. "Nothing new" is
    the normal steady state and "nothing survived" is a bug, and they
    produce identical inserted counts -- so what separates them is
    checked before either is called complete.
    """
    if counters.records_fetched == 0:
        return IngestionStatus.NO_RESULTS

    if counters.inserted == 0 and counters.duplicates == 0:
        # Records arrived and not one of them became or matched a job.
        # Almost always a provider schema change or a filter that has
        # become too strict. Day 6's equivalent of ExtractionStatus.EMPTY.
        return IngestionStatus.ALL_REJECTED

    return IngestionStatus.COMPLETE


def _maybe_external_id(raw: dict[str, Any]) -> str | None:
    """Best-effort ID from a record that failed to parse. Often absent."""
    value = raw.get("id")
    if isinstance(value, (str, int)):
        return str(value)[:255]
    return None


async def run_ingestion(
    source: JobSource,
    *,
    keywords: list[str] | None = None,
    locations: list[str] | None = None,
    max_pages: int | None = None,
    now: datetime | None = None,
) -> IngestionResult:
    """Run one ingestion pass. The single entry point for this module.

    A function rather than requiring callers to build the service, so
    that scripts/ingest_jobs.py today and APScheduler on Day 10 are
    two callers of one thing rather than two places that assemble the
    same pieces slightly differently.
    """
    service = JobIngestionService(source, now=now)
    return await service.run(
        keywords=keywords,
        locations=locations,
        max_pages=max_pages,
    )
