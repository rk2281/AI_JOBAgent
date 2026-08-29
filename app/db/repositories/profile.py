"""Reads and writes for a user's current structured profile."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.profile import Profile


class ProfileRepository:
    """All database access concerning a Profile row.

    There is one Profile per user, always describing the current
    picture — history lives in cv_versions, not here. See
    Profile.active_cv_version_id, which points at the CVVersion this
    row was last built from.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_user_id(self, user_id: int) -> Profile | None:
        result = await self._session.execute(
            select(Profile).where(Profile.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def get_or_create(self, user_id: int) -> Profile:
        existing = await self.get_by_user_id(user_id)
        if existing is not None:
            return existing

        profile = Profile(user_id=user_id, skills=[], experience=[], education=[])
        self._session.add(profile)
        await self._session.flush()
        return profile
