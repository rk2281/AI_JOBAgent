"""Bookkeeping for embedding passes.

One table, existing to answer a question `jobs` cannot: did the last
embedding pass work, and if it produced nothing, WHICH kind of nothing
was it?

This is the Day 6 lesson applied before the bug rather than after it. A
pass that embeds zero rows has at least six causes and only two are
healthy. Worse than ingestion's version, because the failures here are
silent by construction: an unembedded row is not merely unhelpful to
Day 8, it is absent from Day 8 entirely, since `ORDER BY embedding <=>
:q` never returns a NULL row.

Not joined to by matching, and deliberately a separate table rather
than more columns on `jobs` -- the same reasoning as ingestion_runs.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class EmbeddingStatus(str, enum.Enum):
    """How an embedding pass ended.

    A plain VARCHAR, not a native PostgreSQL enum -- same reasoning as
    IngestionStatus, ExtractionStatus and OnboardingState: a native
    enum survives downgrade() as an orphaned type needing a manual
    DROP, and adding a member stays an ordinary code change instead of
    an ALTER TYPE.

    Eight members because "0 rows embedded" has eight meanings:

      everything was already current          NOTHING_TO_DO   <- healthy
      some embedded, some failed              PARTIAL
      all attempted rows embedded             COMPLETE        <- healthy
      the table had no eligible rows at all   NO_SOURCE_ROWS
      every attempted row failed              ALL_FAILED
      the provider could not be reached       PROVIDER_ERROR
      the quota is spent                      QUOTA_EXCEEDED

    NOTHING_TO_DO and NO_SOURCE_ROWS are the pair most worth keeping
    apart, and the pair a single "0 rows" would merge. The first means
    the work is done. The second means there was nothing to work on,
    which on a table that is supposed to hold 99 active jobs means
    something upstream is broken -- ingestion, or the eligibility
    filter in this pass. They read identically in a log and mean
    opposite things.
    """

    RUNNING = "running"

    # Every row that was attempted succeeded. The steady state.
    COMPLETE = "complete"

    # Some succeeded, some failed. Deliberately NOT folded into
    # COMPLETE: a run where half the rows failed is not a completed
    # run, and calling it one puts the failures behind the word people
    # scan past.
    PARTIAL = "partial"

    # Rows exist and every one already has a current embedding. The
    # normal result of running the pass twice.
    NOTHING_TO_DO = "nothing_to_do"

    # The eligibility query matched no rows at all. Not the same as
    # NOTHING_TO_DO. Either the table is empty or the filter is wrong,
    # and both are problems.
    NO_SOURCE_ROWS = "no_source_rows"

    # Rows were attempted and none survived. Almost always ours: a
    # dimension mismatch, an empty document, a rejected task type.
    ALL_FAILED = "all_failed"

    PROVIDER_ERROR = "provider_error"

    # Distinct from PROVIDER_ERROR because the reaction differs.
    # Retrying spends what little is left.
    QUOTA_EXCEEDED = "quota_exceeded"


class EmbeddingRun(Base, TimestampMixin):
    """One execution of an embedding pass over one table.

    The counters are a FUNNEL, not a summary, and the service asserts
    the arithmetic:

        candidates_considered == skipped_empty_text + attempted
        attempted             == succeeded + failed

    Without the assertion a lost row shows up only as a number being
    slightly lower than expected, which nobody notices.

    `remaining_null` is the one counter that is not part of the funnel,
    and it is the most important number in the table. It is measured
    AFTER the pass, by counting rows that still have no embedding. A
    run can report succeeded=40 and still leave 59 rows invisible to
    every Day 8 query; the funnel alone would call that a success.
    """

    __tablename__ = "embedding_runs"

    id: Mapped[int] = mapped_column(primary_key=True)

    scope: Mapped[str] = mapped_column(
        String(32),
        index=True,
        nullable=False,
        doc="Which table this pass covered: 'jobs' or 'cv_versions'.",
    )

    status: Mapped[str] = mapped_column(
        String(32),
        default=EmbeddingStatus.RUNNING.value,
        server_default=EmbeddingStatus.RUNNING.value,
        nullable=False,
        index=True,
    )

    model: Mapped[str | None] = mapped_column(
        String(128),
        doc=(
            "The model this pass used. Recorded per run as well as per "
            "row so that a mixed table can be traced back to the run "
            "that mixed it."
        ),
    )

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    candidates_considered: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        doc="Rows the eligibility query returned as needing work.",
    )

    skipped_empty_text: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        doc=(
            "Selected, but the document builder produced nothing to "
            "embed. A real case: a job row whose title survived "
            "validation and whose description is NULL. Counted rather "
            "than failed, because it is our data problem and not the "
            "provider's."
        ),
    )

    attempted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    succeeded: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    api_calls: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        doc=(
            "Requests sent, not rows embedded. With a batch size of 8 "
            "these differ by roughly eightfold, and this is the one the "
            "quota counts."
        ),
    )

    remaining_null: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        doc=(
            "Rows in scope still holding a NULL embedding when the pass "
            "finished. Measured afterwards, not derived from the funnel. "
            "Anything other than 0 means that many rows are invisible to "
            "every similarity query, and this column is what makes that "
            "visible instead of inferred."
        ),
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        doc="From a describe_*_error() helper only, never str(exc).",
    )
