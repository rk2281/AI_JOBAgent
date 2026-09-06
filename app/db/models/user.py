"""User identity, onboarding progress and career preferences."""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Float, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.cv import CV
    from app.db.models.profile import Profile
    from app.db.models.recommendation import (
        Notification,
        Recommendation,
        UserFeedback,
    )


class OnboardingState(str, enum.Enum):
    """How far a user has progressed through the Telegram onboarding flow.

    Stored as a plain VARCHAR rather than a PostgreSQL ENUM type. Day 2c
    showed what native enums cost: they survive downgrade() as orphaned
    types and have to be dropped by hand. A VARCHAR column with the
    allowed values enforced in Python has none of that, and adding a new
    step later is an ordinary code change instead of an ALTER TYPE.

    Inheriting from str means the value is written and read as its own
    string, so no custom SQLAlchemy type is needed.
    """

    NEW = "new"
    AWAITING_CV = "awaiting_cv"
    AWAITING_ROLES = "awaiting_roles"
    AWAITING_LOCATIONS = "awaiting_locations"
    AWAITING_REMOTE = "awaiting_remote"
    AWAITING_EXPERIENCE = "awaiting_experience"
    AWAITING_THRESHOLD = "awaiting_threshold"

    # Shares the string "complete" with ExtractionStatus.COMPLETE in
    # app/db/models/cv.py — a different column on a different table,
    # tracking a different process. Nothing joins the two, and Python
    # keeps them apart regardless: OnboardingState.COMPLETE ==
    # ExtractionStatus.COMPLETE is False, since they're distinct enum
    # members. The overlap is coincidence, not coupling.
    COMPLETE = "complete"


class PendingPreferenceField(str, enum.Enum):
    """Which free-text preference question a user is mid-answering.

    Not a step of OnboardingState, and not read the same way. This
    exists purely to route the ONE ambiguous case a plain Telegram text
    message creates: a bare string carries no callback_data to
    dispatch on, so something has to remember which question it
    answers. The two closed-choice preference fields (experience,
    notification_threshold) never need this -- they resolve entirely
    through chained callback_data on button taps, which already say
    what they answer.

    ROLES / LOCATIONS only, deliberately not a superset of every
    editable preference.
    """

    ROLES = "roles"
    LOCATIONS = "locations"


class User(Base, TimestampMixin):
    """A person interacting with the bot, identified by Telegram ID."""

    __tablename__ = "users"
    __table_args__ = (
        # Partial index over unfinished onboarding only. The queries
        # that need it ("who dropped out mid-flow?") never ask about
        # completed users, and excluding them keeps the index small as
        # the table grows. Declared here rather than only in the
        # migration so that autogenerate does not later propose
        # dropping an index it cannot see in the metadata.
        Index(
            "ix_users_onboarding_state_pending",
            "onboarding_state",
            postgresql_where=text("onboarding_state <> 'complete'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    telegram_id: Mapped[int] = mapped_column(
        BigInteger,
        unique=True,
        index=True,
        nullable=False,
    )

    username: Mapped[str | None] = mapped_column(String(255))
    full_name: Mapped[str | None] = mapped_column(String(255))

    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    # The single source of truth for where a user is in onboarding.
    # Deliberately not held in python-telegram-bot's in-memory
    # conversation state: that is lost on restart, and a user stranded
    # mid-flow has no way back in.
    onboarding_state: Mapped[str] = mapped_column(
        String(32),
        default=OnboardingState.NEW.value,
        server_default=OnboardingState.NEW.value,
        nullable=False,
    )

    # Which free-text preference question, if any, this user's next
    # plain-text message will answer -- set only by /preferences, and
    # only from behind an onboarding_state == COMPLETE check, because
    # editing one preference presupposes onboarding already produced
    # one. Orthogonal to onboarding_state: that column owns progress
    # through first-time setup; this one owns a short-lived detour
    # taken after setup is finished. They are not meant to both matter
    # at once, and onboarding_state always wins if they do -- any value
    # other than COMPLETE means this column is ignored outright
    # regardless of what it holds, because /restart resets the former
    # without knowing the latter exists. Cleared the instant an answer
    # is saved, and cleared again on the transition into COMPLETE (in
    # _save_threshold), so a value left over from an edit abandoned
    # mid-/restart cannot reactivate and hijack a later, unrelated
    # message.
    pending_preference_field: Mapped[str | None] = mapped_column(String(32))

    profile: Mapped[Profile | None] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )

    preferences: Mapped[UserPreference | None] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )

    cvs: Mapped[list[CV]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )

    recommendations: Mapped[list[Recommendation]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )

    notifications: Mapped[list[Notification]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )

    feedback: Mapped[list[UserFeedback]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )


class UserPreference(Base, TimestampMixin):
    """Career preferences used for filtering and matching."""

    __tablename__ = "user_preferences"

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    target_roles: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        nullable=False,
    )

    preferred_locations: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        nullable=False,
    )

    min_experience_years: Mapped[int | None] = mapped_column(Integer)
    max_experience_years: Mapped[int | None] = mapped_column(Integer)

    remote_only: Mapped[bool] = mapped_column(default=False, nullable=False)

    notification_threshold: Mapped[float] = mapped_column(
        Float,
        default=0.7,
        nullable=False,
    )

    user: Mapped[User] = relationship(back_populates="preferences")
