"""Uploaded CVs and their extracted version history."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.user import User


class ExtractionStatus(str, enum.Enum):
    """How far a CV has progressed through Day 4's text extraction.

    A plain VARCHAR column, not a native PostgreSQL enum type — same
    reasoning as OnboardingState in app/db/models/user.py: a native
    enum survives a migration downgrade() as an orphaned type that
    has to be dropped by hand, and adding a state later becomes an
    ordinary code change instead of an ALTER TYPE.
    """

    PENDING = "pending"
    EXTRACTING = "extracting"

    # Same literal value as OnboardingState.COMPLETE in
    # app/db/models/user.py, on an unrelated column. That's fine: this
    # enum never gets compared against that one, and each is read only
    # against the column it belongs to. Worth a comment because two
    # identically-spelled COMPLETE members would otherwise read like an
    # oversight rather than a deliberate non-issue.
    COMPLETE = "complete"
    FAILED = "failed"

    # The PDF/DOCX has no extractable text layer — a scanned image
    # saved as a PDF, most often. Distinct from FAILED: nothing went
    # wrong, there was simply nothing to read. No multimodal OCR
    # fallback exists yet, so this is a dead end for now, not a retry
    # candidate — see docs/CODEBASE_GUIDE.md.
    NO_TEXT_LAYER = "no_text_layer"

    # Gemini answered successfully and the response parsed, but it
    # carried no skills, no experience, no education and no summary.
    #
    # Deliberately not COMPLETE: Day 4 marked exactly this case
    # complete, and a profile that had silently become empty was
    # indistinguishable from one that was genuinely thin. Deliberately
    # not FAILED either: nothing broke, no exception was raised, and a
    # retry with a better prompt may well succeed, which is not true
    # of FAILED's usual causes.
    EMPTY = "empty"

    # Never written to the extraction_status column. It exists only as
    # a return value from extract_cv, meaning "this extraction
    # finished correctly but a newer CV has since arrived, so the
    # profile was left for that one to write".
    #
    # A returned-only member is unusual enough to be worth stating: the
    # alternative was a separate result enum, which would have made
    # every caller switch on two types to answer one question.
    SUPERSEDED = "superseded"


class CV(Base, TimestampMixin):
    """A CV file uploaded through Telegram."""

    __tablename__ = "cvs"

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    file_type: Mapped[str] = mapped_column(String(32), nullable=False)
    file_size_bytes: Mapped[int | None] = mapped_column(Integer)

    telegram_file_id: Mapped[str | None] = mapped_column(String(255))
    storage_path: Mapped[str | None] = mapped_column(String(1024))

    raw_text: Mapped[str | None] = mapped_column(Text)

    extraction_status: Mapped[str] = mapped_column(
        String(32),
        default=ExtractionStatus.PENDING.value,
        server_default=ExtractionStatus.PENDING.value,
        nullable=False,
    )
    extraction_error: Mapped[str | None] = mapped_column(Text)
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        doc=(
            "Set when the user uploaded a newer CV. NULL means this is "
            "the live one.\n\n"
            "Exists because replacing a CV creates a new cvs row and "
            "leaves the old one behind, often stuck at "
            "extraction_status='pending' forever because "
            "latest_for_user only ever returns the newest. Without "
            "this column that row reads as a bug. With it, the row is "
            "explained: nothing will ever process it because the user "
            "moved on."
        ),
    )

    user: Mapped[User] = relationship(back_populates="cvs")

    versions: Mapped[list[CVVersion]] = relationship(
        back_populates="cv",
        cascade="all, delete-orphan",
        order_by="CVVersion.version.desc()",
    )


class CVVersion(Base, TimestampMixin):
    """A point-in-time extraction from a CV.

    Re-processing a CV creates a new version rather than overwriting,
    which preserves history when the user updates their CV.
    """

    __tablename__ = "cv_versions"
    __table_args__ = (
        UniqueConstraint("cv_id", "version", name="uq_cv_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    cv_id: Mapped[int] = mapped_column(
        ForeignKey("cvs.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    version: Mapped[int] = mapped_column(Integer, nullable=False)

    extracted_profile: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        nullable=False,
    )

    extraction_model: Mapped[str | None] = mapped_column(String(128))

    # Semantic embedding of this specific CV version.
    # Lives here rather than on `profiles` so it can never go stale:
    # each version's embedding describes that version's text and is
    # never updated. `profiles.active_cv_version_id` points to the
    # current one.
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(768),
        nullable=True,
    )

    # The same five columns as jobs, for the same reason: a NULL
    # embedding is invisible rather than wrong. See app/db/models/job.py
    # for the full reasoning.
    #
    # One difference in how they will be used. A job is embedded as a
    # RETRIEVAL_DOCUMENT and a CV version as a RETRIEVAL_QUERY, because
    # a CV is the thing doing the searching. Measured on the live API:
    # the same text under the two task types comes back with cosine
    # 0.861247, so the distinction is real and not decorative.
    embedding_model: Mapped[str | None] = mapped_column(String(128))

    embedded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    embedding_attempts: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )

    embedding_error: Mapped[str | None] = mapped_column(
        Text,
        doc=(
            "From a describe_*_error() helper only, never str(exc). A "
            "provider error on this path can echo back the request, and "
            "the request is a candidate's CV."
        ),
    )

    embedding_source_hash: Mapped[str | None] = mapped_column(String(64))

    cv: Mapped[CV] = relationship(back_populates="versions")