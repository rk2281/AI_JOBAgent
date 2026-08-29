"""The active structured candidate profile."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.user import User


class Profile(Base, TimestampMixin):
    """Structured representation of a candidate, derived from their CV.

    One active profile per user. Historical versions live in
    cv_versions, so this table always holds the current picture.
    """

    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    summary: Mapped[str | None] = mapped_column(Text)

    total_experience_years: Mapped[float | None] = mapped_column()

    current_title: Mapped[str | None] = mapped_column(String(255))
    location: Mapped[str | None] = mapped_column(String(255))

    skills: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        nullable=False,
        doc=(
            "Normalized skill keys, as produced by normalize_skill_name — "
            "lowercased, whitespace-collapsed, punctuation aliases applied, "
            "and de-duplicated. NOT the spellings the CV used.\n\n"
            "This column and cv_versions.extracted_profile['skills'] hold "
            "deliberately different things. This one is the matching "
            "surface: keys here line up with skills.normalized_name, so a "
            "job requiring 'nodejs' matches a candidate whose CV wrote "
            "'Node.js'. The versions table keeps the model's original "
            "wording for display and audit, because normalized keys read "
            "badly to a human ('cpp', 'dotnet') and because an extraction "
            "record should preserve what was actually extracted.\n\n"
            "Anything rendering skills to a user should read the versions "
            "table, or join skills.normalized_name to recover skills.name. "
            "Anything scoring a match should read this column."
        ),
    )

    experience: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        default=list,
        nullable=False,
    )

    education: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        default=list,
        nullable=False,
    )

    active_cv_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("cv_versions.id", ondelete="SET NULL"),
    )

    user: Mapped[User] = relationship(back_populates="profile")
