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
