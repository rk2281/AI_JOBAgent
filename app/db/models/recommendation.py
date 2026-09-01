"""Scoring output, notification history and user feedback."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.user import User


class NotificationStatus(str, enum.Enum):
    """Delivery state of a Telegram notification."""

    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class FeedbackAction(str, enum.Enum):
    """User reaction to a recommended job."""

    INTERESTED = "interested"
    NOT_RELEVANT = "not_relevant"
    SAVED = "saved"


class Recommendation(Base, TimestampMixin):
    """A scored candidate-job pair.

    Individual signal scores are stored alongside the final score so
    that weightings can be re-tuned later without re-running the
    expensive parts of the pipeline.
    """

    __tablename__ = "recommendations"
    __table_args__ = (
        UniqueConstraint("user_id", "job_id", name="uq_recommendation_user_job"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    semantic_score: Mapped[float | None] = mapped_column(Float)
    skill_score: Mapped[float | None] = mapped_column(Float)
    experience_score: Mapped[float | None] = mapped_column(Float)
    location_score: Mapped[float | None] = mapped_column(Float)
    title_score: Mapped[float | None] = mapped_column(Float)

    final_score: Mapped[float] = mapped_column(
        Float,
        index=True,
        nullable=False,
    )

    rank: Mapped[int | None] = mapped_column(Integer)

    semantic_raw: Mapped[float | None] = mapped_column(
        Float,
        doc=(
            "Raw cosine similarity, before rescaling. Stored ALONGSIDE "
            "semantic_score, not instead of it, because the two answer "
            "different questions and using one where the other belongs "
            "is the mistake Day 4 made with status=complete.\n\n"
            "semantic_score is rescaled onto 0-1 against fixed anchors "
            "and feeds the weighted total. THIS column feeds the "
            "notification floor, because an absolute similarity is the "
            "only thing that can say 'nothing today was actually any "
            "good'. A rescaled score cannot: on a day when only "
            "catering jobs were ingested, the best of them still "
            "rescales to something respectable."
        ),
    )

    weight_covered: Mapped[float] = mapped_column(
        Float,
        default=0.0,
        server_default="0",
        nullable=False,
        doc=(
            "Sum of the weights of the signals that did NOT abstain.\n\n"
            "A missing signal abstains and its weight is removed from "
            "the denominator, rather than scoring 0.0 -- otherwise "
            "every non-tech job would rank permanently below every "
            "tech job for a reason that is a data gap, not a fit gap. "
            "But that renormalisation means a score built from 35% of "
            "the weight is not comparable with one built from 100%, "
            "and without this column that difference is invisible: "
            "both are just numbers between 0 and 1.\n\n"
            "NOT NULL with a server default of 0 is safe only because "
            "this table was empty when the column was added (verified: "
            "SELECT count(*) returned 0), so no existing row is given a "
            "coverage it never had."
        ),
    )

    quality_multiplier: Mapped[float] = mapped_column(
        Float,
        default=1.0,
        server_default="1.0",
        nullable=False,
        doc=(
            "Applied to the weighted total: final_score = "
            "weighted_total * quality_multiplier.\n\n"
            "A multiplier, not a sixth weighted signal. A signal "
            "answers 'does this person fit this job'; this answers 'is "
            "this posting a trustworthy description of one'. A "
            "staffing agency's listing is not a worse fit, it is a "
            "less reliable account of one, and folding that into the "
            "weights makes it unexplainable to a user.\n\n"
            "Stored separately from the components so that a score "
            "which was reduced can be seen to have been reduced. "
            "Multiplication rather than subtraction so the result can "
            "never go negative."
        ),
    )

    weights_version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        server_default="1",
        nullable=False,
        doc=(
            "Which set of weights produced this score. Re-tuning the "
            "weights is stated as a goal in this class's own "
            "docstring, so a stored score whose weights are unknown is "
            "a certainty rather than a risk -- and two scores from "
            "different weight sets cannot be compared or ranked "
            "together."
        ),
    )

    inputs_fingerprint: Mapped[str | None] = mapped_column(
        String(64),
        doc=(
            "SHA-256 over everything that fed this score: the "
            "profile's updated_at and sorted skills, the job's "
            "embedding_source_hash and skills_source_hash, and "
            "weights_version. A mismatch against today's inputs means "
            "the stored score is stale.\n\n"
            "Same idea as embedding_source_hash, and the same caveat "
            "is worth stating plainly: this does NOT catch a job "
            "posting changing at the source, because a stored job's "
            "text never changes after insert. It catches OUR inputs "
            "and OUR rules changing."
        ),
    )

    scoring_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("scoring_runs.id", ondelete="SET NULL"),
        index=True,
        doc=(
            "Which run wrote this row. SET NULL rather than CASCADE: "
            "deleting an old run's bookkeeping must not delete the "
            "recommendations, and Day 11's notifications and feedback "
            "hold foreign keys to them."
        ),
    )

    match_reasons: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        nullable=False,
    )

    user: Mapped[User] = relationship(back_populates="recommendations")


class Notification(Base, TimestampMixin):
    """Record of a recommendation delivered to Telegram.

    Prevents sending the same job to the same user twice.
    """

    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("user_id", "job_id", name="uq_notification_user_job"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )

    recommendation_id: Mapped[int | None] = mapped_column(
        ForeignKey("recommendations.id", ondelete="SET NULL"),
    )

    status: Mapped[NotificationStatus] = mapped_column(
        Enum(NotificationStatus, name="notification_status"),
        default=NotificationStatus.PENDING,
        nullable=False,
    )

    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    error_message: Mapped[str | None] = mapped_column(Text)

    user: Mapped[User] = relationship(back_populates="notifications")


class UserFeedback(Base, TimestampMixin):
    """A user's reaction to a recommended job.

    Collected now, used later to personalise ranking weights.
    """

    __tablename__ = "user_feedback"

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )

    recommendation_id: Mapped[int | None] = mapped_column(
        ForeignKey("recommendations.id", ondelete="SET NULL"),
    )

    action: Mapped[FeedbackAction] = mapped_column(
        Enum(FeedbackAction, name="feedback_action"),
        nullable=False,
    )

    user: Mapped[User] = relationship(back_populates="feedback")
