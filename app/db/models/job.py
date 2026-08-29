"""Normalized job postings and their skill requirements."""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class Job(Base, TimestampMixin):
    """A job posting normalized into a common schema.

    Jobs from any source land in this one shape, which is what lets
    additional sources be added later without touching matching logic.
    """

    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_job_source_external"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    source: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)

    title: Mapped[str] = mapped_column(String(512), nullable=False)
    company: Mapped[str | None] = mapped_column(String(255))
    location: Mapped[str | None] = mapped_column(String(255), index=True)

    description: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)

    is_remote: Mapped[bool] = mapped_column(default=False, nullable=False)

    min_experience_years: Mapped[int | None] = mapped_column(Integer)
    max_experience_years: Mapped[int | None] = mapped_column(Integer)

    posted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        index=True,
    )

    content_hash: Mapped[str | None] = mapped_column(
        String(64),
        index=True,
    )

    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    # Semantic embedding of the job text, generated on Day 7.
    # Nullable because a job is ingested (Day 6) before it is embedded;
    # a failed or pending API call must not block storing the job.
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(768),
        nullable=True,
    )

    job_skills: Mapped[list[JobSkill]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
    )


class JobSkill(Base):
    """Association between a job and a required skill."""

    __tablename__ = "job_skills"

    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"),
        primary_key=True,
    )

    skill_id: Mapped[int] = mapped_column(
        ForeignKey("skills.id", ondelete="CASCADE"),
        primary_key=True,
    )

    is_required: Mapped[bool] = mapped_column(default=True, nullable=False)

    job: Mapped[Job] = relationship(back_populates="job_skills")