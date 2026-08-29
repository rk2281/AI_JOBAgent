"""Reads and writes for uploaded CV files."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.cv import CV, CVVersion, ExtractionStatus


class CVRepository:
    """All database access concerning a CV row."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        user_id: int,
        file_name: str,
        file_type: str,
        file_size_bytes: int | None,
        telegram_file_id: str | None,
        storage_path: str | None,
    ) -> CV:
        """Record an uploaded CV.

        raw_text is deliberately left NULL. Extraction is Day 4's job;
        Day 3 only proves the bytes arrived and are safe to keep.
        """
        cv = CV(
            user_id=user_id,
            file_name=file_name,
            file_type=file_type,
            file_size_bytes=file_size_bytes,
            telegram_file_id=telegram_file_id,
            storage_path=storage_path,
        )
        self._session.add(cv)
        await self._session.flush()
        return cv

    async def latest_for_user(self, user_id: int) -> CV | None:
        result = await self._session.execute(
            select(CV)
            .where(CV.user_id == user_id)
            .order_by(CV.created_at.desc(), CV.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def count_for_user(self, user_id: int) -> int:
        result = await self._session.execute(
            select(CV.id).where(CV.user_id == user_id)
        )
        return len(result.scalars().all())

    async def by_id(self, cv_id: int) -> CV | None:
        """Load one CV by primary key.

        Needed because extraction now spans two transactions: the row
        object loaded in the first is bound to a session that has
        closed by the time the second begins, so the second reloads it
        rather than carrying a detached instance across.
        """
        result = await self._session.execute(select(CV).where(CV.id == cv_id))
        return result.scalar_one_or_none()

    async def claim_for_extraction(
        self,
        cv_id: int,
        *,
        stale_after: timedelta,
    ) -> bool:
        """Try to take exclusive ownership of extracting this CV.

        Returns True if this caller won the claim, False if another
        extraction already holds it.

        Day 4 wrote extraction_status='extracting' but never read it
        back, so it recorded intent without preventing anything. Two
        tasks could extract the same CV at once, both compute the same
        MAX(version) + 1, and collide on uq_cv_version. Making the
        write conditional turns the same column into an actual lock:
        PostgreSQL evaluates the WHERE and applies the UPDATE as one
        atomic statement, so exactly one of two concurrent callers can
        see a rowcount of 1.

        The staleness window is what stops a crashed process stranding
        a CV at 'extracting' forever. A claim older than stale_after is
        assumed abandoned and may be taken over. It is a window, not a
        guarantee — a very slow but living extraction could in
        principle be stolen from — which is why it is set well beyond
        any plausible Gemini call rather than trimmed to fit one.

        updated_at is set explicitly. The ORM's onupdate fires for ORM
        flushes, not for a Core UPDATE like this one, so leaving it out
        would freeze the timestamp the staleness check depends on.
        """
        cutoff = datetime.now(UTC) - stale_after

        statement = (
            update(CV)
            .where(
                CV.id == cv_id,
                or_(
                    CV.extraction_status != ExtractionStatus.EXTRACTING.value,
                    CV.updated_at < cutoff,
                ),
            )
            .values(
                extraction_status=ExtractionStatus.EXTRACTING.value,
                updated_at=datetime.now(UTC),
            )
        )

        result = await self._session.execute(statement)
        return result.rowcount == 1

    async def create_version(
        self,
        cv_id: int,
        extracted_profile: dict[str, Any],
        extraction_model: str,
    ) -> CVVersion:
        """Record one extraction of a CV as a new, numbered version.

        Re-extracting a CV (a retry, or a future re-run with a better
        model) adds a version rather than overwriting the last one, so
        which model produced which profile stays visible. The version
        number is one past the current maximum for this CV, computed
        with MAX() rather than counting rows, so a version that was
        later deleted can never be reissued and silently collide with
        history that still references it.
        """
        result = await self._session.execute(
            select(func.max(CVVersion.version)).where(CVVersion.cv_id == cv_id)
        )
        next_version = (result.scalar() or 0) + 1

        version = CVVersion(
            cv_id=cv_id,
            version=next_version,
            extracted_profile=extracted_profile,
            extraction_model=extraction_model,
        )
        self._session.add(version)
        await self._session.flush()
        return version
