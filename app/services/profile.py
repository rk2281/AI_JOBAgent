"""Reading a candidate's profile back to them, and replacing their CV.

Both commands are deliberately thin. /profile only ever reads — it
does not start an extraction, retry a failed one, or write anything.
A read command that quietly spends a Gemini call is a bad surprise on
a free tier of roughly twenty requests a day, and a user who ran
/profile twice would have no idea they had caused work.

/update_cv writes nothing either. It cannot: Telegram delivers the
file in a later, separate update. All it does is tell the user what to
send, which the existing document handler already accepts for a user
whose onboarding is COMPLETE.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user import OnboardingState
from app.db.repositories.cv import CVRepository
from app.db.repositories.profile import ProfileRepository
from app.db.repositories.user import UserRepository
from app.services.profile_view import ProfileSnapshot, render_profile
from app.services.replies import BotReply

logger = logging.getLogger(__name__)


class ProfileService:
    """Day 5's two commands.

    Takes a session like OnboardingService does, because both of these
    are ordinary read paths with no network call in the middle. That
    is the distinction from extract_cv, which owns its own
    transactions precisely because it does have one.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._users = UserRepository(session)
        self._cvs = CVRepository(session)
        self._profiles = ProfileRepository(session)

    async def show(self, telegram_id: int) -> BotReply:
        """Render the user's current profile. Reads only."""
        user = await self._users.get_by_telegram_id(telegram_id)
        if user is None:
            return render_profile(ProfileSnapshot(has_user=False))

        cv = await self._cvs.latest_for_user(user.id)
        if cv is None:
            return render_profile(ProfileSnapshot(has_user=True, has_cv=False))

        # The JSON comes from the version the profile was last built
        # from, not from the latest version of the latest CV. Those
        # differ when an extraction produced something unusable and was
        # refused: the version row exists for audit, but
        # active_cv_version_id still points at the good one, and that
        # is the one the user should see.
        extracted: dict | None = None
        profile = await self._profiles.get_by_user_id(user.id)
        if profile is not None and profile.active_cv_version_id is not None:
            version = await self._cvs.version_by_id(profile.active_cv_version_id)
            if version is not None:
                extracted = version.extracted_profile

        return render_profile(
            ProfileSnapshot(
                has_user=True,
                has_cv=True,
                cv_file_name=cv.file_name,
                extraction_status=cv.extraction_status,
                extraction_error=cv.extraction_error,
                extracted_profile=extracted,
            )
        )

    async def request_update(self, telegram_id: int) -> BotReply:
        """Ask the user to send a replacement CV.

        Sets no state. OnboardingService.handle_document already
        accepts a document from a COMPLETE user and treats it as a
        replacement, so introducing an AWAITING_CV detour here would
        add a state the user could get stranded in for no gain — and
        would contradict the Day 2d rule that onboarding_state is the
        single record of progress.
        """
        user = await self._users.get_by_telegram_id(telegram_id)
        if user is None:
            return BotReply(text="I don't know you yet — send /start to begin.")

        state = OnboardingState(user.onboarding_state)
        if state is not OnboardingState.COMPLETE:
            return BotReply(
                text=(
                    "Let's finish setting you up first — send /start to "
                    "carry on where you left off."
                )
            )

        return BotReply(
            text=(
                "📎 Send me your new CV as a PDF or DOCX.\n\n"
                "I'll read it and replace your current profile. Your "
                "preferences stay as they are, and if the new file turns "
                "out to be unreadable you'll keep the profile you have."
            )
        )
