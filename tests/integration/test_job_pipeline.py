"""Day 12 integration test 2 -- raw job -> validate -> dedup -> database.

    FakeJobSource (a JobSource, no network)
        -> app.services.job_ingestion.run_ingestion   (REAL)
        -> jobs / ingestion_runs / ingestion_rejects  (REAL PostgreSQL)

`JobSource` is a Protocol precisely so a fake is an ordinary object,
which is why substituting Adzuna here costs nothing in fidelity: every
rule under test -- validation, the freshness filter, both dedup paths,
the funnel assertion, the reject rows -- runs exactly as it does in
production. The only thing not exercised is Adzuna's own response
parsing, which `tests/test_job_ingestion.py` covers separately.

The funnel is the thing worth checking against a real database rather
than a fake one: `records_fetched` must equal the sum of the five exit
counters, and that sum includes `inserted`, which only a real INSERT
against a real unique constraint can be trusted to produce.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from app.db.models.ingestion import IngestionReject, IngestionRun
from app.db.models.job import Job
from app.db.session import session_scope
from app.integrations.adzuna import SearchPage, UnparseableRecord
from app.schemas.job import RawJobPosting
from app.services.job_ingestion import IngestionStatus, run_ingestion

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


class FakeJobSource:
    """Serves prepared pages. Satisfies the JobSource Protocol."""

    source_name = "fakesource"

    def __init__(self, pages: list[SearchPage]) -> None:
        self._pages = pages
        self.calls: list[dict[str, Any]] = []

    async def search(
        self,
        *,
        what: str = "",
        where: str = "",
        page: int = 1,
        max_days_old: int | None = None,
        results_per_page: int | None = None,
    ) -> SearchPage:
        self.calls.append(
            {
                "what": what,
                "where": where,
                "page": page,
                "max_days_old": max_days_old,
            }
        )
        if page <= len(self._pages):
            return self._pages[page - 1]
        return SearchPage(total_available=0, postings=[], unparseable=[])


def _posting(
    external_id: str,
    *,
    title: str = "AI Engineer",
    company: str | None = "Northwind Analytics",
    location: str | None = "Delhi, India",
    url: str = "https://example.test/jobs/1",
    days_old: int = 1,
    description: str | None = "Build ML systems in Python and FastAPI.",
) -> RawJobPosting:
    return RawJobPosting(
        source="fakesource",
        external_id=external_id,
        title=title,
        company=company,
        location=location,
        url=url,
        description=description,
        posted_at=NOW - timedelta(days=days_old),
    )


def _page(postings: list[RawJobPosting], unparseable: int = 0) -> SearchPage:
    return SearchPage(
        total_available=len(postings),
        postings=postings,
        unparseable=[
            UnparseableRecord(raw={"broken": index}, reason="missing title")
            for index in range(unparseable)
        ],
    )


def test_the_ingestion_funnel_balances_against_a_real_database(
    run_with_database: Callable[[Callable[[], Awaitable[Any]]], Any],
) -> None:
    """Six records in; every one leaves through exactly one counter.

    The six are chosen so that five of the funnel's exits are used at
    once: a good job, a second good job, a job whose URL is not a link,
    a job with no posting date, a job older than the freshness window,
    and a repeat of the first by external_id.
    """
    postings = [
        _posting("job-1", title="AI Engineer"),
        _posting("job-2", title="NLP Engineer", url="https://example.test/jobs/2"),
        _posting("job-3", url="ftp://example.test/jobs/3"),  # validation_failed
        _posting("job-5", days_old=90),  # filtered_out (too old)
        _posting("job-1"),  # duplicate by (source, external_id)
    ]
    no_date = _posting("job-4")
    no_date = no_date.model_copy(update={"posted_at": None})  # validation_failed
    postings.insert(3, no_date)

    source = FakeJobSource([_page(postings)])

    async def body() -> dict[str, Any]:
        result = await run_ingestion(
            source,
            keywords=["ai engineer"],
            locations=["delhi"],
            max_pages=1,
            now=NOW,
        )

        async with session_scope() as session:
            jobs = (await session.execute(select(Job))).scalars().all()
            run = (await session.execute(select(IngestionRun))).scalar_one()
            rejects = (
                (await session.execute(select(IngestionReject.reason))).scalars().all()
            )
            return {
                "result": result,
                "titles": sorted(job.title for job in jobs),
                "external_ids": sorted(job.external_id for job in jobs),
                "hashes": [job.content_hash for job in jobs],
                "last_seen": [job.last_seen_at for job in jobs],
                "run_status": run.status,
                "run_inserted": run.inserted,
                "run_finished_at": run.finished_at,
                "reject_reasons": rejects,
            }

    observed = run_with_database(body)
    counters = observed["result"].counters

    assert counters["records_fetched"] == 6
    assert counters["inserted"] == 2
    assert counters["validation_failed"] == 2
    assert counters["filtered_out"] == 1
    assert counters["duplicates"] == 1

    # The funnel assertion, checked here rather than trusted.
    accounted = (
        counters["normalize_failed"]
        + counters["validation_failed"]
        + counters["filtered_out"]
        + counters["duplicates"]
        + counters["inserted"]
    )
    assert accounted == counters["records_fetched"]

    assert observed["titles"] == ["AI Engineer", "NLP Engineer"]
    assert observed["run_status"] == IngestionStatus.COMPLETE.value
    assert observed["run_inserted"] == 2
    assert observed["run_finished_at"] is not None

    # Each rejection says WHY, in a row a human can read later.
    assert len(observed["reject_reasons"]) == 2
    assert any("link" in reason for reason in observed["reject_reasons"])
    assert any("posting date" in reason for reason in observed["reject_reasons"])

    # Every stored job carries a content hash. The unique constraint on
    # that column is the second dedup path, and a NULL there would
    # disable it silently -- PostgreSQL treats NULLs as distinct.
    assert all(h for h in observed["hashes"])
    assert all(seen == NOW for seen in observed["last_seen"])


def test_the_same_posting_under_a_new_id_is_caught_by_content_hash(
    run_with_database: Callable[[Callable[[], Awaitable[Any]]], Any],
) -> None:
    """The second dedup path: same title/company/location, new id.

    This is the one that needs a real database. `by_content_hash` is a
    query, and `uq_job_content_hash` is the constraint standing behind
    it -- the constraint that `alembic revision --autogenerate` was
    about to drop before Day 12 declared it in the model.
    """

    async def body() -> dict[str, Any]:
        first = FakeJobSource([_page([_posting("agency-a")])])
        await run_ingestion(first, keywords=["ai"], locations=["delhi"], max_pages=1, now=NOW)

        # Same job, re-listed by the board under a different id.
        second = FakeJobSource(
            [_page([_posting("agency-b", url="https://example.test/jobs/other")])]
        )
        result = await run_ingestion(
            second, keywords=["ai"], locations=["delhi"], max_pages=1, now=NOW
        )

        async with session_scope() as session:
            count = (await session.execute(select(func.count(Job.id)))).scalar_one()
            job = (await session.execute(select(Job))).scalar_one()
            return {
                "result": result,
                "job_count": count,
                "external_id": job.external_id,
            }

    observed = run_with_database(body)

    assert observed["result"].counters["duplicates"] == 1
    assert observed["result"].counters["inserted"] == 0
    assert observed["job_count"] == 1
    # The FIRST row survives. A repost must not overwrite the id a
    # user's recommendations and notifications already point at.
    assert observed["external_id"] == "agency-a"


def test_running_the_same_ingestion_twice_inserts_nothing_the_second_time(
    run_with_database: Callable[[Callable[[], Awaitable[Any]]], Any],
) -> None:
    """Day 12 section 26: idempotency, stated as a test.

    The second run must also REFRESH last_seen_at, because that column
    is the input to retirement -- a repeat run that dedups correctly
    but forgets to refresh would age out jobs the source is still
    advertising.
    """
    later = NOW + timedelta(days=2)

    async def body() -> dict[str, Any]:
        # Different TITLES, not just different ids: content_hash covers
        # title/company/location, so two postings differing only by
        # external_id are one job by design. Writing this fixture with
        # the same title made the first run insert 1 of 2 -- the dedup
        # rule working, and a reminder that "two postings" and "two
        # jobs" are different things here.
        page = _page(
            [
                _posting("job-1", title="AI Engineer"),
                _posting(
                    "job-2",
                    title="NLP Engineer",
                    url="https://example.test/2",
                ),
            ]
        )

        first = await run_ingestion(
            FakeJobSource([page]), keywords=["ai"], locations=["delhi"], max_pages=1, now=NOW
        )
        second = await run_ingestion(
            FakeJobSource([page]),
            keywords=["ai"],
            locations=["delhi"],
            max_pages=1,
            now=later,
        )

        async with session_scope() as session:
            count = (await session.execute(select(func.count(Job.id)))).scalar_one()
            seen = (await session.execute(select(Job.last_seen_at))).scalars().all()
            runs = (await session.execute(select(func.count(IngestionRun.id)))).scalar_one()
            return {
                "first": first,
                "second": second,
                "job_count": count,
                "last_seen": sorted(seen),
                "run_count": runs,
            }

    observed = run_with_database(body)

    assert observed["first"].counters["inserted"] == 2
    assert observed["second"].counters["inserted"] == 0
    assert observed["second"].counters["duplicates"] == 2
    assert observed["job_count"] == 2

    # Both rows moved forward in time.
    assert all(seen == later for seen in observed["last_seen"])

    # Two runs, both recorded. An idempotent OUTCOME is not the same as
    # a run that did not happen, and the ingestion_runs row is what
    # tells them apart.
    assert observed["run_count"] == 2


def test_a_page_of_unreadable_records_is_not_reported_as_an_empty_page(
    run_with_database: Callable[[Callable[[], Awaitable[Any]]], Any],
) -> None:
    """Day 12 section 27: malformed job data.

    A provider schema change produces a page of unparseable records.
    That must not read as "the job market was quiet today" -- one is
    our bug, the other is the world.
    """

    async def body() -> dict[str, Any]:
        source = FakeJobSource([_page([], unparseable=3)])
        result = await run_ingestion(
            source, keywords=["ai"], locations=["delhi"], max_pages=1, now=NOW
        )

        async with session_scope() as session:
            rejects = (
                (await session.execute(select(IngestionReject.stage))).scalars().all()
            )
            return {"result": result, "reject_stages": rejects}

    observed = run_with_database(body)
    counters = observed["result"].counters

    assert counters["records_fetched"] == 3
    assert counters["normalize_failed"] == 3
    assert counters["inserted"] == 0
    assert observed["result"].status is not IngestionStatus.NO_RESULTS
    # The payloads survive. They are the only evidence of what changed.
    assert len(observed["reject_stages"]) == 3
