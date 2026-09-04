"""Reads and writes for users and their preferences."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user import OnboardingState, User, UserPreference


class UserRepository:
    """All database access concerning a User row."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        result = await self._session.execute(
            select(User).where(User.telegram_id == telegram_id)
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, user_id: int) -> User | None:
        """Fetch by internal id.

        Notification delivery works from `recommendations.user_id`, an
        internal id, and needs the telegram_id and is_active flag that
        live on the user row. Every other caller in the project starts
        from a Telegram update and so starts from a telegram_id.
        """
        return await self._session.get(User, user_id)

    async def deactivate(self, user: User) -> None:
        """Stop notifying this user.

        Called when Telegram reports the chat is permanently
        undeliverable -- the bot was blocked, or the account is gone.
        Reuses the existing `is_active` flag rather than introducing a
        second notion of user status: two columns that both mean
        "should we contact them" is two columns that can disagree.

        Note what this does NOT do: it does not stop the user being
        SCORED. `select_target_user_ids` draws from Profile.user_id and
        is unchanged, so their recommendations keep being computed and
        are waiting intact if they unblock the bot. Only delivery
        consults this flag.
        """
        user.is_active = False
        await self._session.flush()

    async def get_or_create(
        self,
        telegram_id: int,
        username: str | None = None,
        full_name: str | None = None,
    ) -> tuple[User, bool]:
        """Fetch the user for this Telegram ID, creating one if absent.

        Returns (user, created). The INSERT uses ON CONFLICT DO NOTHING
        against the unique index on telegram_id rather than a
        check-then-insert, because two updates from the same person can
        be processed concurrently — tapping /start twice is enough — and
        the naive version loses that race with an IntegrityError.
        """
        existing = await self.get_by_telegram_id(telegram_id)
        if existing is not None:
            # Telegram usernames and display names change. Keep them fresh.
            if username is not None and existing.username != username:
                existing.username = username
            if full_name is not None and existing.full_name != full_name:
                existing.full_name = full_name
            return existing, False

        statement = (
            insert(User)
            .values(
                telegram_id=telegram_id,
                username=username,
                full_name=full_name,
                onboarding_state=OnboardingState.NEW.value,
            )
            .on_conflict_do_nothing(index_elements=["telegram_id"])
            .returning(User)
        )

        result = await self._session.execute(statement)
        created = result.scalar_one_or_none()

        if created is not None:
            return created, True

        # The conflict fired: another update inserted this user between
        # our SELECT and our INSERT. Read the winner's row.
        await self._session.flush()
        user = await self.get_by_telegram_id(telegram_id)
        assert user is not None, "row vanished after ON CONFLICT DO NOTHING"
        return user, False

    async def set_onboarding_state(
        self,
        user: User,
        state: OnboardingState,
    ) -> None:
        user.onboarding_state = state.value
        await self._session.flush()

    async def get_preferences(self, user_id: int) -> UserPreference | None:
        result = await self._session.execute(
            select(UserPreference).where(UserPreference.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def get_or_create_preferences(self, user_id: int) -> UserPreference:
        """Return the user's preference row, creating an empty one if needed.

        Onboarding fills this in over several steps, so the row has to
        exist before the first answer arrives. The column defaults on
        UserPreference make an empty row valid on its own.
        """
        existing = await self.get_preferences(user_id)
        if existing is not None:
            return existing

        preferences = UserPreference(
            user_id=user_id,
            target_roles=[],
            preferred_locations=[],
        )
        self._session.add(preferences)
        await self._session.flush()
        return preferences
