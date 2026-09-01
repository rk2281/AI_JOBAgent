"""Bookkeeping for scoring passes.

One table, existing to answer a question `recommendations` cannot: did
the last scoring run work, and if it produced no notifications, WHICH
kind of nothing was that?

This is the third time this shape has been needed. Ingestion has six
statuses because "0 jobs added" had six causes; embedding has eight
because "0 rows embedded" had eight. Scoring has the same problem in a
worse form, because its failures all produce numbers. A run that
scores every job at zero, a run that ranks everything identically, and
a run that silently drops every job with a missing signal all finish
successfully, write rows, and report a full funnel.

The counters are a FUNNEL, and the service asserts the arithmetic:

    users_considered == users_skipped_no_cv + users_scored
    jobs_considered  == jobs_skipped_no_embedding
                        + jobs_excluded_manual
                        + jobs_scored

If an assertion here fires, suspect the model before the data. On Day
7 the funnel check fired twice and both times the data was fine -- the
model had no name for rows left behind by an aborted run. That was
worth more than a passing check.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class ScoringStatus(str, enum.Enum):
    """How a scoring pass ended.

    A plain VARCHAR, not a native PostgreSQL enum -- same reasoning as
    IngestionStatus, EmbeddingStatus, ExtractionStatus and
    OnboardingState: a native enum survives downgrade() as an orphaned
    type needing a manual DROP.

    The pair most worth keeping apart is COMPLETE_NO_QUALIFYING and
    ALL_ABSTAINED. Both mean "nobody got notified". The first is the
    system working and honestly reporting a quiet day. The second
    means every signal on every pair had no data, so the scores are
    arithmetic performed on nothing. They read identically in a log.
    """

    RUNNING = "running"

    # Scored, and at least one pair cleared the notification gate.
    COMPLETE = "complete"

    # Scoring ran correctly and nothing cleared the gate. HEALTHY.
    # This is what "no good matches today" looks like, and it must be
    # distinguishable from the four failures below at a glance.
    COMPLETE_NO_QUALIFYING = "complete_no_qualifying"

    # Some users or jobs failed while others succeeded. Deliberately
    # not folded into COMPLETE: a run where half the pairs failed is
    # not a completed run.
    PARTIAL = "partial"

    # No active job had an embedding. Upstream is broken -- ingestion
    # or the embedding pass -- and scoring is the wrong place to look.
    NO_CANDIDATE_JOBS = "no_candidate_jobs"

    # No user had an embedded active CV version.
    NO_SCORABLE_USERS = "no_scorable_users"

    # Every pair was scored and every signal abstained, so
    # weight_covered was 0 throughout. The funnel looks perfect. This
    # is the exact failure the abstain rule makes possible, and it is
    # what today would produce if the enrichment pass never ran: skills
    # (30%) and experience (20%) both have no job-side data.
    ALL_ABSTAINED = "all_abstained"

    # Every final score came out identical, so the ranking carries no
    # information. Detected from distinct_score_count, not guessed at.
    DEGENERATE = "degenerate"

    FAILED = "failed"


class ScoringRun(Base, TimestampMixin):
    """One execution of a scoring pass."""

    __tablename__ = "scoring_runs"

    id: Mapped[int] = mapped_column(primary_key=True)

    status: Mapped[str] = mapped_column(
        String(32),
        default=ScoringStatus.RUNNING.value,
        server_default=ScoringStatus.RUNNING.value,
        nullable=False,
        index=True,
    )

    weights_version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        server_default="1",
        nullable=False,
    )

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        doc=(
            "Written before any work happens, so a pass killed "
            "mid-flight leaves a row stuck at 'running' rather than no "
            "row at all. A crash that erases its own evidence is the "
            "hardest kind to investigate."
        ),
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # --- funnel: users -----------------------------------------------------
    users_considered: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    users_skipped_no_cv: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        doc=(
            "No embedded active CV version. search_for_user() returns "
            "None rather than [] for exactly this case: 'could not "
            "search' and 'searched and found nothing' are different, "
            "and this counter is where that difference survives."
        ),
    )
    users_scored: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # --- funnel: jobs ------------------------------------------------------
    jobs_considered: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    jobs_skipped_no_embedding: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        doc=(
            "A job with a NULL embedding is not ranked low, it is "
            "ABSENT -- `ORDER BY embedding <=> :q` never returns a "
            "NULL row. This counter is the only place that exclusion "
            "produces a number a person can read."
        ),
    )
    jobs_excluded_manual: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    jobs_scored: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    pairs_scored: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        doc="user-job pairs, i.e. rows written to recommendations.",
    )

    # --- abstains, per signal ----------------------------------------------
    #
    # Per signal rather than one total, because the answers differ.
    # abstain_skill == pairs_scored means the 30% signal did nothing at
    # all, which today it would, since job_skills is empty. A single
    # combined counter would show a large number and not say which.
    abstain_semantic: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    abstain_skill: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    abstain_experience: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    abstain_location: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    abstain_title: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # --- semantic rescaling health -----------------------------------------
    #
    # Counted with STRICT comparisons: raw > anchor_high and
    # raw < anchor_low. A raw value of exactly anchor_high maps to 1.0
    # by arithmetic without being clamped, and counting it here would
    # report a loss of discrimination that did not happen.
    semantic_clamped_high: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        doc=(
            "Pairs whose raw similarity exceeded the top anchor and "
            "were flattened to 1.0. A run where 30 pairs clamped means "
            "the anchors are wrong; without this counter it reads as "
            "30 excellent matches."
        ),
    )
    semantic_clamped_low: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    semantic_raw_min: Mapped[float | None] = mapped_column(Float)
    semantic_raw_max: Mapped[float | None] = mapped_column(Float)
    semantic_raw_median: Mapped[float | None] = mapped_column(
        Float,
        doc=(
            "These three make anchor drift readable instead of "
            "inferred. The anchors were fitted to one CV against 99 "
            "jobs; a different CV may sit somewhere else entirely, and "
            "this is how that is noticed rather than assumed away."
        ),
    )

    # --- quality penalties -------------------------------------------------
    quality_penalty_agency: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        doc=(
            "Pairs that took the agency multiplier. 0 over a corpus "
            "known to contain 29 such jobs means the settings list is "
            "stale or the company matching is broken -- and neither "
            "would show up anywhere else."
        ),
    )
    quality_penalty_no_city: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )

    # --- enrichment reach --------------------------------------------------
    jobs_remote: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        doc=(
            "Before enrichment this is 0 for all 99 rows, because "
            "is_remote is hardcoded False at ingestion. A zero AFTER "
            "enrichment means the inference rule never fired."
        ),
    )
    jobs_hybrid: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # --- score distribution ------------------------------------------------
    score_min: Mapped[float | None] = mapped_column(Float)
    score_max: Mapped[float | None] = mapped_column(Float)
    score_median: Mapped[float | None] = mapped_column(Float)

    distinct_score_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        doc=(
            "How many distinct final scores this run produced.\n\n"
            "The single most useful number in this table. A run with "
            "pairs_scored = 99 and distinct_score_count = 1 ranked "
            "everything identically -- a total failure that passes "
            "every other check here, since the funnel balances, the "
            "status is complete, and 99 rows were written."
        ),
    )

    notify_eligible: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        doc=(
            "Pairs clearing all three gates: final_score >= the user's "
            "notification_threshold, semantic_raw >= the absolute "
            "floor, and weight_covered >= the minimum coverage. Day 8 "
            "computes this; Day 11 acts on it."
        ),
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        doc="From a describe_*_error() helper only, never str(exc).",
    )
