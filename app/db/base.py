"""Declarative base and shared column mixins.

Every model in the application inherits from Base so SQLAlchemy can
collect them into a single metadata registry. Alembic reads that
registry to generate migrations.
"""

from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Shared parent for all ORM models."""


class TimestampMixin:
    """Adds created_at / updated_at maintained by the database."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
