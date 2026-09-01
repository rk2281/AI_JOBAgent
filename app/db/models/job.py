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

    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        index=True,
        doc=(
            "When an ingestion run last saw this posting in the source's "
            "results.\n\n"
            "This is how a repost is handled. The same job appearing again "
            "30 days later refreshes this row rather than creating a "
            "second one, so a user is never re-notified about a posting "
            "they already dismissed -- and the duplicate never reaches "
            "Day 7's embedding call or Day 8's scoring, which is where it "
            "would actually cost something.\n\n"
            "It is also the input to expiry. A job unseen for longer than "
            "job_retire_after_days becomes is_active=False. Note what this "
            "does NOT mean: the source never says a vacancy has closed, "
            "and our queries are keyword-scoped, so a posting absent from "
            "today's results may simply not have matched today's search. "
            "Unseen is a proxy for closed, not a synonym."
        ),
    )

    is_active: Mapped[bool] = mapped_column(
        default=True,
        nullable=False,
        doc=(
            "False means retired for going unseen, not deleted. The row "
            "stays so that a job which reappears keeps its history and its "
            "embedding rather than being ingested afresh."
        ),
    )

    # Semantic embedding of the job text, generated on Day 7.
    # Nullable because a job is ingested (Day 6) before it is embedded;
    # a failed or pending API call must not block storing the job.
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(768),
        nullable=True,
    )

    # The five columns below exist because a NULL embedding is
    # INVISIBLE rather than wrong. `ORDER BY embedding <=> :q` never
    # returns a NULL row, so a job that failed to embed silently drops
    # out of every Day 8 query with no error anywhere. NULL alone
    # cannot tell "not attempted yet" from "attempted and failed", and
    # those need opposite responses.
    embedding_model: Mapped[str | None] = mapped_column(
        String(128),
        doc=(
            "Which model produced this vector. Not optional bookkeeping: "
            "gemini-embedding-001 and gemini-embedding-2 both return 768 "
            "dimensions, so rows from the two are identical in the column "
            "and meaningless as neighbours of each other. This is the only "
            "thing that makes such a mix detectable."
        ),
    )

    embedded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    embedding_attempts: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
        doc=(
            "0 with a NULL embedding means never attempted. Above 0 with "
            "a NULL embedding means attempted and failed. The distinction "
            "is what stops a permanently broken row from being retried "
            "forever, and what stops a never-tried row from being "
            "mistaken for a broken one."
        ),
    )

    embedding_error: Mapped[str | None] = mapped_column(
        Text,
        doc=(
            "Written only from a describe_*_error() helper, never from "
            "str(exc). Same rule as ingestion_runs.error_message: a "
            "provider exception's string form can carry the request that "
            "produced it, and on this path that request is job text."
        ),
    )

    embedding_source_hash: Mapped[str | None] = mapped_column(
        String(64),
        doc=(
            "SHA-256 of the exact text that was embedded, so a stale "
            "vector is detectable.\n\n"
            "Worth being precise about what this does NOT guard, because "
            "the obvious reading is wrong. It does not catch the source "
            "changing a posting: compute_content_hash() covers title, "
            "company and location only, and mark_seen() refreshes neither "
            "the description nor anything else, so a stored job's text "
            "never changes after insert. What it catches is OUR text "
            "rule changing. The day build_job_document() gains a field or "
            "alters its format, every stored vector becomes stale while "
            "still looking perfectly valid."
        ),
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