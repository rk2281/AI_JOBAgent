"""Normalized job postings and their skill requirements."""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
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
        # Day 12. Both of the objects below already exist in the database
        # and are created by migrations; neither was declared here. That
        # gap is not cosmetic. `alembic revision --autogenerate` compares
        # the DATABASE against THIS metadata, so an object the metadata
        # does not know about reads as an object somebody dropped by
        # hand, and the generated migration helpfully drops it for real.
        #
        # Run on 2026-09-05 against a schema built from these migrations,
        # `alembic check` proposed exactly that: remove uq_job_content_hash,
        # remove ix_jobs_embedding_hnsw, remove ix_cv_versions_embedding_hnsw,
        # remove ix_agent_runs_unfinished. Dropping the unique constraint
        # is the dangerous one -- ingestion's duplicate defence would be
        # gone, no test would fail (the suite has no database), and the
        # symptom would be duplicate jobs appearing weeks later.
        #
        # Declaring them keeps the metadata honest. It changes no DDL:
        # the constraint and the index are already in the database, so
        # this makes `alembic check` pass rather than making it produce
        # a migration.
        UniqueConstraint("content_hash", name="uq_job_content_hash"),
        Index(
            "ix_jobs_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
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
        # NOT index=True. Migration d7a3f1c92b40 dropped the plain index
        # this once declared and created uq_job_content_hash in its
        # place, promoting the hash from a stored value into an enforced
        # identity. The model kept saying "index"; the database has said
        # "unique constraint" since Day 6. See __table_args__.
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

    # --- job-skill extraction bookkeeping (Day 8) ---------------------------
    #
    # Exactly the same shape and exactly the same reasoning as the
    # five embedding columns above, because the failure is the same
    # failure. `job_skills` holding no rows for a job means BOTH
    # "never extracted" and "extracted, found nothing", and those need
    # opposite responses: the first is work still to do, the second is
    # a job that scores an ABSTAIN on the 30% skill signal rather than
    # a zero.
    #
    # Without these, a scoring run over 99 jobs that extracted skills
    # for none of them looks identical to one where every job was
    # examined and genuinely lists no recognisable skill.

    skills_extracted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )

    skills_extraction_model: Mapped[str | None] = mapped_column(
        String(128),
        doc=(
            "Which generation model produced these skills. Recorded "
            "per row for the same reason as embedding_model: two "
            "models can populate the same table with results that are "
            "indistinguishable in the columns and not comparable with "
            "each other."
        ),
    )

    skills_extraction_attempts: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
        doc=(
            "0 with no job_skills rows means never attempted. Above 0 "
            "with no rows means attempted -- either it failed, or the "
            "description genuinely named nothing. This is what stops a "
            "permanently unextractable row from being retried on every "
            "run, and what stops a never-tried row from being mistaken "
            "for one."
        ),
    )

    skills_extraction_error: Mapped[str | None] = mapped_column(
        Text,
        doc=(
            "Written only from a describe_*_error() helper, never from "
            "str(exc). A google-genai error can echo back the request, "
            "and on this path the request is the job description."
        ),
    )

    skills_source_hash: Mapped[str | None] = mapped_column(
        String(64),
        doc=(
            "SHA-256 of the text the extractor read, which is "
            "build_job_document(title, description) -- the SAME "
            "function the embedding uses.\n\n"
            "Sharing that function is deliberate: the vector and the "
            "extracted skills then describe the same job, and the day "
            "build_job_document() changes, both stored artefacts go "
            "stale together and both become detectable by comparing "
            "this against embedding_source_hash's input."
        ),
    )

    work_mode: Mapped[str | None] = mapped_column(
        String(16),
        doc=(
            "'remote', 'hybrid' or 'onsite'. NULL means not determined.\n\n"
            "A separate column rather than reusing is_remote because "
            "is_remote is a boolean and therefore has no way to say "
            "'we have not looked'. Today it is False on all 99 rows -- "
            "not because they are all onsite, but because "
            "adzuna.py:285 hardcodes False, since Adzuna has no remote "
            "field. That is the NULL-versus-0.0 problem in a column "
            "type that cannot express NULL.\n\n"
            "Three values rather than two booleans: 'remote AND hybrid' "
            "is not a coherent state, and two booleans would make it "
            "representable. Hybrid is kept distinct from remote because "
            "treating them as the same annoys users in both directions."
        ),
    )

    is_excluded: Mapped[bool] = mapped_column(
        default=False,
        server_default="false",
        nullable=False,
        doc=(
            "Set by hand for postings that are not job postings -- "
            "currently one row, a recruiter advertising his own "
            "LinkedIn profile.\n\n"
            "A boolean and a manual one, not a classifier. Writing a "
            "junk detector for a single known row means writing "
            "something whose false positives nobody would ever see. "
            "An excluded job is still COUNTED, as "
            "scoring_runs.jobs_excluded_manual -- it is skipped, not "
            "made invisible."
        ),
    )

    exclusion_reason: Mapped[str | None] = mapped_column(
        Text,
        doc="Why this row was excluded by hand. Required reading later.",
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