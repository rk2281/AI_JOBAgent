"""The Telegram onboarding flow, as a state machine over the database.

Design note — why there is no ConversationHandler here.

python-telegram-bot ships ConversationHandler, which tracks where each
user is in a multi-step dialogue. It keeps that state in memory. Restart
the process and every half-onboarded user is stranded: the bot has
forgotten the question it asked, and they have no way to resume.

The alternative is persistence, which gives two stores that both claim
to know the user's position and can disagree. Day 2d settled on a
principle for exactly this shape of problem: prefer designs where the
bad state cannot be represented over designs where it is merely
avoided. So users.onboarding_state is the only record of progress. It
is read at the start of every update and written at the end. There is
nothing for it to fall out of sync with.

The cost is that branching is written by hand instead of declared. At
seven steps that is a small, readable dispatch table.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user import OnboardingState, User
from app.db.repositories.cv import CVRepository
from app.db.repositories.user import UserRepository
from app.services.cv_intake import CVIntakeService, CVValidationError
from app.services.replies import BotReply, Button

logger = logging.getLogger(__name__)

# Supplied by the handler: fetches the uploaded file's bytes from Telegram.
DownloadCallable = Callable[[], Awaitable[bytes]]

MAX_LIST_ITEMS = 10
MAX_ITEM_LENGTH = 80


# Experience brackets offered as buttons. Free text would mean parsing
# "about 3 yrs", "3+", "three" and every other way people write it;
# buttons make the answer a closed set and the parsing disappears.
EXPERIENCE_CHOICES: dict[str, tuple[int, int | None]] = {
    "0-1": (0, 1),
    "1-3": (1, 3),
    "3-5": (3, 5),
    "5-8": (5, 8),
    "8+": (8, None),
}

THRESHOLD_CHOICES: dict[str, float] = {
    "0.6": 0.6,
    "0.7": 0.7,
    "0.8": 0.8,
}

CALLBACK_PREFIX = "onb"


@dataclass(frozen=True)
class DocumentOutcome:
    """What handling one document upload produced.

    `stored` is True only when this call actually created a new cvs
    row — not when the upload was rejected for a bad extension or bad
    magic bytes, and not when it never reached the CV step at all. A
    plain BotReply return value cannot carry that distinction without
    a caller inferring it from wording, which is exactly the kind of
    coupling this exists to avoid: the handler uses `stored` to decide
    whether to schedule extraction, without needing to know or guess
    what the text of an acceptance reply looks like.

    `user_id` (the users.id primary key, not the Telegram ID) rides
    along for the same reason: handlers are not allowed to query
    repositories directly (see app/db/repositories/__init__.py), so
    without this the handler would have no legitimate way to learn
    which user_id to hand to app.services.cv_extraction.extract_cv.
    """

    reply: BotReply
    stored: bool = False
    user_id: int | None = None


def parse_list_input(raw: str) -> list[str]:
    """Turn "Backend Engineer, ML engineer , backend engineer" into a list.

    Splits on commas and newlines, trims, drops blanks, and removes
    case-insensitive duplicates while keeping the first spelling the
    user typed. Capped at MAX_LIST_ITEMS so one pasted CV section
    cannot become a hundred "preferences".
    """
    parts: list[str] = []
    for chunk in raw.replace("\n", ",").split(","):
        cleaned = " ".join(chunk.split())
        if cleaned:
            parts.append(cleaned[:MAX_ITEM_LENGTH])

    seen: set[str] = set()
    unique: list[str] = []
    for part in parts:
        key = part.casefold()
        if key not in seen:
            seen.add(key)
            unique.append(part)

    return unique[:MAX_LIST_ITEMS]


class OnboardingService:
    """Drives a user from /start to a complete profile and preferences."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._users = UserRepository(session)
        self._cvs = CVRepository(session)
        self._intake = CVIntakeService()

    # -- entry points -----------------------------------------------------

    async def start(
        self,
        telegram_id: int,
        username: str | None,
        full_name: str | None,
    ) -> BotReply:
        """Handle /start: create the user if new, then resume where they are."""
        user, created = await self._users.get_or_create(
            telegram_id=telegram_id,
            username=username,
            full_name=full_name,
        )

        if created:
            logger.info("Registered new user telegram_id=%s", telegram_id)

        state = OnboardingState(user.onboarding_state)

        if state is OnboardingState.NEW:
            await self._users.set_onboarding_state(
                user, OnboardingState.AWAITING_CV
            )
            greeting = (
                f"👋 Welcome{', ' + full_name.split()[0] if full_name else ''}!\n\n"
                "I find jobs that actually match you, and message you when "
                "one comes up.\n\n"
                "This takes about a minute. First, send me your CV as a "
                "PDF or DOCX file."
            )
            return BotReply(text=greeting)

        if state is OnboardingState.COMPLETE:
            return BotReply(
                text=(
                    "You're already set up. ✅\n\n"
                    "Use /status to see what I have on file, or /restart to "
                    "go through setup again."
                )
            )

        # Mid-flow. Re-ask the current question rather than starting over,
        # so a restart or a lost message costs one question, not the lot.
        return BotReply(
            text="Picking up where we left off.\n\n"
            + self._prompt_for(state).text,
            buttons=self._prompt_for(state).buttons,
        )

    async def restart(self, telegram_id: int) -> BotReply:
        """Send an existing user back to the beginning of the flow."""
        user = await self._users.get_by_telegram_id(telegram_id)
        if user is None:
            return await self.start(telegram_id, None, None)

        await self._users.set_onboarding_state(user, OnboardingState.AWAITING_CV)
        return BotReply(
            text=(
                "Starting over. Your previous CVs are kept, not deleted.\n\n"
                "Send me your CV as a PDF or DOCX file."
            )
        )

    async def status(self, telegram_id: int) -> BotReply:
        """Report what the bot currently holds for this user."""
        user = await self._users.get_by_telegram_id(telegram_id)
        if user is None:
            return BotReply(text="I don't know you yet — send /start to begin.")

        state = OnboardingState(user.onboarding_state)
        preferences = await self._users.get_preferences(user.id)
        cv = await self._cvs.latest_for_user(user.id)

        lines = [f"Setup: {'complete ✅' if state is OnboardingState.COMPLETE else 'in progress'}"]

        lines.append(f"CV: {cv.file_name if cv else 'not uploaded yet'}")

        if preferences is not None:
            roles = ", ".join(preferences.target_roles) or "—"
            locations = ", ".join(preferences.preferred_locations) or "—"

            lines.append(f"Target roles: {roles}")
            lines.append(
                f"Locations: {'remote only' if preferences.remote_only else locations}"
            )

            if preferences.min_experience_years is not None:
                upper = preferences.max_experience_years
                span = (
                    f"{preferences.min_experience_years}+"
                    if upper is None
                    else f"{preferences.min_experience_years}–{upper}"
                )
                lines.append(f"Experience: {span} years")

            lines.append(f"Alert threshold: {preferences.notification_threshold:.2f}")

        if state is not OnboardingState.COMPLETE:
            lines.append("\nSend /start to continue setup.")

        return BotReply(text="\n".join(lines))

    # -- step handlers ----------------------------------------------------

    async def handle_document(
        self,
        telegram_id: int,
        file_name: str | None,
        size_bytes: int | None,
        telegram_file_id: str | None,
        download: DownloadCallable,
    ) -> DocumentOutcome:
        """Accept a CV upload.

        `download` is a coroutine supplied by the handler that fetches
        the bytes from Telegram. Passing it in rather than importing the
        bot here keeps this service free of Telegram, and means the
        download only happens after the cheap metadata checks pass.
        """
        user = await self._users.get_by_telegram_id(telegram_id)
        if user is None:
            return DocumentOutcome(reply=await self.start(telegram_id, None, None))

        state = OnboardingState(user.onboarding_state)

        if state not in {OnboardingState.AWAITING_CV, OnboardingState.COMPLETE}:
            return DocumentOutcome(
                reply=BotReply(
                    text="Let's finish the current question first.\n\n"
                    + self._prompt_for(state).text,
                    buttons=self._prompt_for(state).buttons,
                )
            )

        try:
            file_type = self._intake.validate_metadata(file_name, size_bytes)
        except CVValidationError as error:
            return DocumentOutcome(reply=BotReply(text=f"⚠️ {error}"))

        data = await download()

        try:
            stored_cv = self._intake.save(user.id, file_type, data)
        except CVValidationError as error:
            return DocumentOutcome(reply=BotReply(text=f"⚠️ {error}"))

        await self._cvs.create(
            user_id=user.id,
            file_name=file_name or f"cv.{file_type}",
            file_type=file_type,
            file_size_bytes=stored_cv.size_bytes,
            telegram_file_id=telegram_file_id,
            storage_path=stored_cv.storage_path,
        )

        if state is OnboardingState.COMPLETE:
            # A returning user replacing their CV, not onboarding.
            return DocumentOutcome(
                reply=BotReply(
                    text=(
                        f"✅ Got it — {file_name}. Saved as your latest CV.\n\n"
                        "Your preferences are unchanged."
                    )
                ),
                stored=True,
                user_id=user.id,
            )

        await self._users.set_onboarding_state(
            user, OnboardingState.AWAITING_ROLES
        )

        return DocumentOutcome(
            reply=BotReply(
                text=(
                    f"✅ Got it — {file_name}.\n\n"
                    + self._prompt_for(OnboardingState.AWAITING_ROLES).text
                )
            ),
            stored=True,
            user_id=user.id,
        )

    async def handle_text(self, telegram_id: int, text: str) -> BotReply:
        """Route a plain text message according to the user's current step."""
        user = await self._users.get_by_telegram_id(telegram_id)
        if user is None:
            return await self.start(telegram_id, None, None)

        state = OnboardingState(user.onboarding_state)

        if state is OnboardingState.AWAITING_ROLES:
            return await self._save_roles(user, text)

        if state is OnboardingState.AWAITING_LOCATIONS:
            return await self._save_locations(user, text)

        if state is OnboardingState.AWAITING_CV:
            return BotReply(
                text=(
                    "I need your CV as a file, not as a message. "
                    "Attach a PDF or DOCX and send it."
                )
            )

        if state in {
            OnboardingState.AWAITING_REMOTE,
            OnboardingState.AWAITING_EXPERIENCE,
            OnboardingState.AWAITING_THRESHOLD,
        }:
            prompt = self._prompt_for(state)
            return BotReply(
                text="Please tap one of the buttons below.\n\n" + prompt.text,
                buttons=prompt.buttons,
            )

        return BotReply(
            text=(
                "You're all set up. Use /status to see your settings, "
                "or /restart to redo them."
            )
        )

    async def handle_callback(self, telegram_id: int, data: str) -> BotReply:
        """Route an inline-button tap.

        Callback data looks like "onb:remote:yes". The state is re-read
        from the database and checked against the button's step, because
        old messages stay tappable forever — a user can scroll up a week
        later and press a button from a finished flow.
        """
        user = await self._users.get_by_telegram_id(telegram_id)
        if user is None:
            return await self.start(telegram_id, None, None)

        parts = data.split(":")
        if len(parts) != 3 or parts[0] != CALLBACK_PREFIX:
            logger.warning("Unrecognised callback data: %s", data)
            return BotReply(text="That button has expired. Send /start.")

        _, step, value = parts
        state = OnboardingState(user.onboarding_state)

        expected = {
            "remote": OnboardingState.AWAITING_REMOTE,
            "exp": OnboardingState.AWAITING_EXPERIENCE,
            "threshold": OnboardingState.AWAITING_THRESHOLD,
        }.get(step)

        if expected is None:
            return BotReply(text="That button has expired. Send /start.")

        if state is not expected:
            prompt = self._prompt_for(state)
            return BotReply(
                text="That button is from an earlier step.\n\n" + prompt.text,
                buttons=prompt.buttons,
            )

        if step == "remote":
            return await self._save_remote(user, value == "yes")
        if step == "exp":
            return await self._save_experience(user, value)
        return await self._save_threshold(user, value)

    # -- individual steps -------------------------------------------------

    async def _save_roles(self, user: User, text: str) -> BotReply:
        roles = parse_list_input(text)
        if not roles:
            return BotReply(
                text="I didn't catch any roles there. Try: "
                "Backend Engineer, ML Engineer"
            )

        preferences = await self._users.get_or_create_preferences(user.id)
        preferences.target_roles = roles
        await self._session.flush()

        await self._users.set_onboarding_state(
            user, OnboardingState.AWAITING_LOCATIONS
        )

        return BotReply(
            text=f"Noted: {', '.join(roles)}.\n\n"
            + self._prompt_for(OnboardingState.AWAITING_LOCATIONS).text
        )

    async def _save_locations(self, user: User, text: str) -> BotReply:
        locations = parse_list_input(text)
        if not locations:
            return BotReply(
                text="I didn't catch any locations. Try: Delhi, Noida, Gurgaon"
            )

        preferences = await self._users.get_or_create_preferences(user.id)
        preferences.preferred_locations = locations
        await self._session.flush()

        await self._users.set_onboarding_state(
            user, OnboardingState.AWAITING_REMOTE
        )

        prompt = self._prompt_for(OnboardingState.AWAITING_REMOTE)
        return BotReply(
            text=f"Noted: {', '.join(locations)}.\n\n{prompt.text}",
            buttons=prompt.buttons,
        )

    async def _save_remote(self, user: User, remote_only: bool) -> BotReply:
        preferences = await self._users.get_or_create_preferences(user.id)
        preferences.remote_only = remote_only
        await self._session.flush()

        await self._users.set_onboarding_state(
            user, OnboardingState.AWAITING_EXPERIENCE
        )

        prompt = self._prompt_for(OnboardingState.AWAITING_EXPERIENCE)
        answer = "Remote only." if remote_only else "Open to on-site roles."
        return BotReply(
            text=f"{answer}\n\n{prompt.text}",
            buttons=prompt.buttons,
        )

    async def _save_experience(self, user: User, choice: str) -> BotReply:
        bracket = EXPERIENCE_CHOICES.get(choice)
        if bracket is None:
            return BotReply(text="That option isn't one I recognise. Send /start.")

        minimum, maximum = bracket

        preferences = await self._users.get_or_create_preferences(user.id)
        preferences.min_experience_years = minimum
        preferences.max_experience_years = maximum
        await self._session.flush()

        await self._users.set_onboarding_state(
            user, OnboardingState.AWAITING_THRESHOLD
        )

        prompt = self._prompt_for(OnboardingState.AWAITING_THRESHOLD)
        return BotReply(
            text=f"{choice} years of experience.\n\n{prompt.text}",
            buttons=prompt.buttons,
        )

    async def _save_threshold(self, user: User, choice: str) -> BotReply:
        threshold = THRESHOLD_CHOICES.get(choice)
        if threshold is None:
            return BotReply(text="That option isn't one I recognise. Send /start.")

        preferences = await self._users.get_or_create_preferences(user.id)
        preferences.notification_threshold = threshold
        await self._session.flush()

        await self._users.set_onboarding_state(user, OnboardingState.COMPLETE)

        logger.info("Onboarding complete for user_id=%s", user.id)

        return BotReply(
            text=(
                "🎉 All set!\n\n"
                f"I'll alert you about jobs scoring {threshold:.0%} or higher.\n\n"
                "Use /status any time to review this, or /restart to change it. "
                "Job matching goes live once ingestion is running."
            )
        )

    # -- prompts ----------------------------------------------------------

    def _prompt_for(self, state: OnboardingState) -> BotReply:
        """The question that belongs to a given step.

        Kept in one place so that resuming a flow and asking for the
        first time produce identical wording — there is only one copy of
        each question.
        """
        if state is OnboardingState.AWAITING_CV:
            return BotReply(text="Send me your CV as a PDF or DOCX file.")

        if state is OnboardingState.AWAITING_ROLES:
            return BotReply(
                text=(
                    "What roles are you targeting?\n"
                    "Send them separated by commas — for example:\n"
                    "Backend Engineer, ML Engineer, Data Scientist"
                )
            )

        if state is OnboardingState.AWAITING_LOCATIONS:
            return BotReply(
                text=(
                    "Which locations work for you?\n"
                    "For example: Delhi, Noida, Bangalore"
                )
            )

        if state is OnboardingState.AWAITING_REMOTE:
            return BotReply(
                text="Do you want remote roles only?",
                buttons=[
                    [
                        Button("Remote only", f"{CALLBACK_PREFIX}:remote:yes"),
                        Button("On-site is fine", f"{CALLBACK_PREFIX}:remote:no"),
                    ]
                ],
            )

        if state is OnboardingState.AWAITING_EXPERIENCE:
            return BotReply(
                text="How many years of experience do you have?",
                buttons=[
                    [
                        Button(label, f"{CALLBACK_PREFIX}:exp:{label}")
                        for label in ("0-1", "1-3", "3-5")
                    ],
                    [
                        Button(label, f"{CALLBACK_PREFIX}:exp:{label}")
                        for label in ("5-8", "8+")
                    ],
                ],
            )

        if state is OnboardingState.AWAITING_THRESHOLD:
            return BotReply(
                text=(
                    "Last one. How selective should I be?\n\n"
                    "I only message you about jobs scoring above this.\n"
                    "0.6 — more jobs, looser matches\n"
                    "0.7 — balanced (recommended)\n"
                    "0.8 — fewer jobs, stronger matches"
                ),
                buttons=[
                    [
                        Button("0.6", f"{CALLBACK_PREFIX}:threshold:0.6"),
                        Button("0.7 ⭐", f"{CALLBACK_PREFIX}:threshold:0.7"),
                        Button("0.8", f"{CALLBACK_PREFIX}:threshold:0.8"),
                    ]
                ],
            )

        return BotReply(text="You're all set. Use /status to review your settings.")
