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
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.user import User


class NotificationStatus(str, enum.Enum):
    """Delivery state of a Telegram notification.

    READ THIS BEFORE WRITING SQL AGAINST notifications.status.

    SQLAlchemy's Enum() persists a Python enum by its NAME, not its
    value, so the labels in PostgreSQL are 'PENDING', 'SENT' and
    'FAILED' -- uppercase -- while `NotificationStatus.SENT.value` is
    the lowercase "sent". Confirmed against pg_enum, not remembered.

    The partial unique index in c8e2a15f4b93 therefore says
    `WHERE status = 'SENT'`. Written lowercase with a ::text cast it
    would be created successfully and match nothing forever, which is
    duplicate prevention that reports success while enforcing nothing.
    """

    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


# How a notification came to be sent. A plain string, not a fourth
# PostgreSQL enum type: Day 2c showed those survive downgrade() as
# orphaned types needing a hand-written drop, which is why
# users.onboarding_state is a VARCHAR too.
#
# The distinction is not cosmetic. It is what lets a human ask "did the
# production gate ever actually fire?" of a table that also holds rows
# put there by a person testing delivery by hand. Without it the first
# manual test would make the answer unknowable forever.
TRIGGER_SOURCE_SCHEDULED = "scheduled"
TRIGGER_SOURCE_MANUAL_TEST = "manual_test"

NOTIFICATION_TRIGGER_SOURCES = frozenset(
    {TRIGGER_SOURCE_SCHEDULED, TRIGGER_SOURCE_MANUAL_TEST}
)


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
    """One ATTEMPT to deliver a recommendation to Telegram.

    An attempt, not a delivery. That is the Day 11 change and it is the
    whole point of the table's new shape.

    Until Day 11 this carried `UniqueConstraint(user_id, job_id)`, one
    row per pair forever. Read as "prevents sending the same job to the
    same user twice" that looks exactly right, and it does prevent
    that. What it also did was make a FAILURE permanent: a Telegram
    outage during the only attempt wrote a `failed` row that occupied
    the pair's single slot, and the user was locked out of that job for
    good with no way back. The rule that was actually wanted was never
    "one row" -- it was "at most one SUCCESS".

    So the constraint is now partial: at most one row per (user_id,
    job_id) WHERE status = 'SENT'. Any number of `pending` and `failed`
    rows may sit alongside it, and the sequence

        failed -> failed -> sent

    is a legal, fully recorded history rather than a lost user.

    ENFORCED BY THE DATABASE, NOT BY A CHECK IN THE SERVICE

    The service does check before sending, because a query is cheaper
    than a message and a friendlier error. But two processes can
    interleave two such checks and both conclude "not sent yet", and no
    amount of application care fixes that from inside one of them. The
    index is what makes a second success impossible rather than
    unlikely, and the service treats its IntegrityError as the
    duplicate signal rather than as a crash.

    NO ATTEMPT CEILING. Deliberately no `max_attempts` column and no
    counter to compare one against. A ceiling would let a transient
    outage cost a user a job permanently -- the exact failure the old
    unique constraint produced, reintroduced with a number attached.
    Failures stay visible as rows; a human reads the count.
    """

    __tablename__ = "notifications"
    __table_args__ = (
        # Declared on the model as well as in the migration so that
        # `alembic revision --autogenerate` cannot later propose
        # dropping an index it cannot see in Base.metadata -- which is
        # what it did twice to the two HNSW indexes (see Day 4).
        #
        # 'SENT' is uppercase because that is the enum LABEL in
        # PostgreSQL. See NotificationStatus.
        Index(
            "uq_notification_sent_user_job",
            "user_id",
            "job_id",
            unique=True,
            postgresql_where=text("status = 'SENT'"),
        ),
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

    error_message: Mapped[str | None] = mapped_column(
        Text,
        doc=(
            "Why this attempt failed, in words safe to read back.\n\n"
            "NEVER str(exc) on a Telegram exception. The Bot API "
            "carries the bot token in the URL PATH -- every call is "
            "https://api.telegram.org/bot<TOKEN>/sendMessage -- so a "
            "network error whose httpx cause is formatted into this "
            "column writes a live credential into the database "
            "permanently, to be read back weeks later by a human with "
            "no idea what they are looking at. That is the Adzuna "
            "app_key incident with the credential moved one URL "
            "component over.\n\n"
            "Everything written here comes from "
            "describe_telegram_error(), which reports an exception's "
            "class and the API's own description field and nothing "
            "else."
        ),
    )

    trigger_source: Mapped[str] = mapped_column(
        String(32),
        default=TRIGGER_SOURCE_SCHEDULED,
        server_default=TRIGGER_SOURCE_SCHEDULED,
        nullable=False,
        doc=(
            "'scheduled' or 'manual_test'. Which path produced this "
            "attempt.\n\n"
            "NOT NULL with a server default is safe only because this "
            "table was empty when the column was added (verified: "
            "SELECT count(*) returned 0), so no existing row is given "
            "a provenance it never had. Same argument as "
            "recommendations.weight_covered, and it is an argument "
            "about a count on a particular day, not a general licence."
        ),
    )

    user: Mapped[User] = relationship(back_populates="notifications")


class UserFeedback(Base, TimestampMixin):
    """A user's reaction to a recommended job.

    Collected now, used later to personalise ranking weights.

    THE CONSTRAINT IS THREE COLUMNS, AND THE THIRD ONE IS THE POINT

    UNIQUE(user_id, job_id, action), not UNIQUE(user_id, job_id). The
    difference is whether a person is allowed to change their mind.

        Interested, Interested   -> one row.  A double tap is not two
                                   opinions, and the timestamp keeps
                                   meaning when the opinion was FIRST
                                   held.
        Interested, Saved        -> two rows. Different questions.
        Interested, Not Relevant -> two rows. A contradiction, kept on
                                   purpose: what someone thought before
                                   they read the description is signal,
                                   and the pair of rows is a stronger
                                   signal than either alone.

    A two-column constraint would collapse all three cases to one row
    and silently drop the second, DIFFERENT action -- while the handler
    still acknowledged it, so the user would be told their Not Relevant
    was recorded when nothing had been written. Feedback that reports
    success without persisting is worse than feedback that errors.

    Inserts go through ON CONFLICT DO NOTHING rather than an upsert, so
    a repeat never overwrites the original row's created_at. DO UPDATE
    would quietly move the timestamp forward on every stray tap and
    make "when did they first say this" unanswerable.
    """

    __tablename__ = "user_feedback"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "job_id",
            "action",
            name="uq_user_feedback_user_job_action",
        ),
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

    action: Mapped[FeedbackAction] = mapped_column(
        Enum(FeedbackAction, name="feedback_action"),
        nullable=False,
    )

    user: Mapped[User] = relationship(back_populates="feedback")
