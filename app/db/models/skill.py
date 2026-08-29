"""Normalized skills catalog.

Skills arrive from CVs and job descriptions in many spellings:
"Node.js", "NodeJS", "node js". Storing a normalized_name lets
matching compare them reliably.
"""

from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Skill(Base, TimestampMixin):
    """A single canonical skill."""

    __tablename__ = "skills"

    id: Mapped[int] = mapped_column(primary_key=True)

    name: Mapped[str] = mapped_column(String(128), nullable=False)

    normalized_name: Mapped[str] = mapped_column(
        String(128),
        unique=True,
        index=True,
        nullable=False,
    )

    category: Mapped[str | None] = mapped_column(String(64))
