"""What happened during an ingestion run, and what it threw away.

Two tables, both existing to answer questions that the `jobs` table
cannot:

  ingestion_runs    -- did the last run work, and if it produced
                       nothing, which KIND of nothing was it
  ingestion_rejects -- why did this specific job never appear

Neither is joined to by matching. That is the point of them being
separate tables rather than columns or states on `jobs`: `jobs` is the
surface Day 8 scores against, and a rejected or diagnostic row living
in it would have to be excluded by every future query, silently
polluting whichever one forgot. The same shape of mistake as
cvs.superseded_at, which had to be added afterwards for exactly this
reason.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class IngestionStatus(str, enum.Enum):
    """How an ingestion run ended.

    A plain VARCHAR column, not a native PostgreSQL enum -- same
    reasoning as ExtractionStatus and OnboardingState: a native enum
    survives a migration downgrade() as an orphaned type that must be
    dropped by hand, and adding a member later stays an ordinary code
    change instead of an ALTER TYPE.

    The reason there are six of these rather than the obvious three is
    the lesson Day 4 and Day 5 taught at some cost. A run can end with
    zero new jobs for at least six different reasons, and only one of
    them is healthy:

      the source returned nothing                    NO_RESULTS
      everything returned failed our own rules       ALL_REJECTED
      everything returned was already stored         COMPLETE  <- normal
      everything returned was filtered out           COMPLETE
      the source errored                             SOURCE_ERROR
      the quota is spent                             QUOTA_EXCEEDED

    A single "0 jobs added" collapses all six into the number that
    will be looked at most often and tells the least. Day 4 wrote
    status=complete for extractions that produced entirely empty
    results, and a silently emptied profile became indistinguishable
    from a genuinely thin one. This enum is that fix applied before
    the bug rather than after it.
    """

    RUNNING = "running"

    # Records were fetched and at least one was stored or recognised
    # as already stored. The steady state: on most days almost
    # everything is a duplicate, and that is success, not silence.
    COMPLETE = "complete"

    # The source answered normally and had nothing to give. Not a
    # failure -- there may genuinely be no new postings in the window.
    # Separate from COMPLETE because a run of these in a row means the
    # query is too narrow, which is a real problem wearing a calm face.
    NO_RESULTS = "no_results"

    # Records came back, and none of them survived our own parsing,
    # validation and filtering. This is Day 6's EMPTY: the run worked,
    # the source answered, and nothing got through. Almost always our
    # bug, most often a provider schema change, and it must never be
    # mistaken for a quiet day.
    ALL_REJECTED = "all_rejected"

    SOURCE_ERROR = "source_error"

    # Distinct from SOURCE_ERROR because the reaction differs: this is
    # not transient, retrying spends what little is left, and Adzuna's
    # free quota is monthly rather than hourly, so it will still be
    # spent tomorrow.
    QUOTA_EXCEEDED = "quota_exceeded"


class IngestionRun(Base, TimestampMixin):
    """One execution of the ingestion pipeline.

    The counters are a FUNNEL, not a summary, and that is the whole
    design. Every record the source returned leaves through exactly
    one of them, so:

        records_fetched == normalize_failed + validation_failed
                           + filtered_out + duplicates + inserted

    must hold at the end of every run. The service asserts it. If it
    ever fails, a record went missing somewhere in the pipeline -- and
    without the assertion that loss would show up only as a number
    being slightly lower than expected, which nobody notices.
    """

    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(primary_key=True)

    source: Mapped[str] = mapped_column(String(64), index=True, nullable=False)

    status: Mapped[str] = mapped_column(
        String(32),
        default=IngestionStatus.RUNNING.value,
        server_default=IngestionStatus.RUNNING.value,
        nullable=False,
        index=True,
    )

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    queries_attempted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pages_fetched: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        doc="One page is one API call. This is what the monthly quota counts.",
    )

    records_fetched: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    normalize_failed: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        doc="Could not be read as a job at all -- a provider schema change.",
    )
    validation_failed: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        doc="Read fine, but missing something we require to store it.",
    )
    filtered_out: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        doc="Valid, but outside the freshness window we asked for.",
    )
    duplicates: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        doc="Already stored. Refreshed rather than inserted. NOT a failure.",
    )
    inserted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    retired: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        doc="Jobs marked inactive by this run for going unseen too long.",
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        doc=(
            "Written only from describe_http_error(), never from str(exc). "
            "Adzuna's credentials are URL query parameters and httpx puts "
            "the URL in the exception's string form, so a raw exception "
            "stored here would be a permanent credential leak in the "
            "database."
        ),
    )

    rejects: Mapped[list[IngestionReject]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )


class IngestionReject(Base):
    """One record that was fetched but never became a job.

    Exists to answer "why did this job never appear" three weeks
    later, which is unanswerable from the jobs table by definition --
    the row is not there. Stores the raw payload because when a
    provider changes its schema this is the only evidence of what
    changed.

    Not a state on `jobs`. See this module's docstring.
    """

    __tablename__ = "ingestion_rejects"

    id: Mapped[int] = mapped_column(primary_key=True)

    run_id: Mapped[int] = mapped_column(
        ForeignKey("ingestion_runs.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    source: Mapped[str] = mapped_column(String(64), nullable=False)

    external_id: Mapped[str | None] = mapped_column(
        String(255),
        doc="Nullable: a record that failed to normalize may not have had one.",
    )

    stage: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        doc="'normalize' or 'validate' -- which gate it failed at.",
    )

    reason: Mapped[str] = mapped_column(Text, nullable=False)

    raw_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    run: Mapped[IngestionRun] = relationship(back_populates="rejects")
