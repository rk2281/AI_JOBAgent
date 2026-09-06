"""Edit one preference field without touching a CV.

/restart forces a full CV re-upload (an extraction call and an
embedding call) to change a single answer, because onboarding_state
has exactly one way back to AWAITING_ROLES: through AWAITING_CV. This
is the narrower path CLAUDE.md's original ask exists for: change
target_roles, preferred_locations, the experience bracket, or
notification_threshold, and nothing else.

Deliberately mirrors OnboardingService's shape (session in, BotReply
out, no Telegram import) but is NOT a second copy of its state
machine. OnboardingService's _save_* methods each advance to the NEXT
onboarding step; reusing that machinery here would push a user editing
one field back into full onboarding. What IS reused -- parse_list_input,
EXPERIENCE_CHOICES, THRESHOLD_CHOICES -- is the validation, not the
transitions, so a value typed here is accepted or rejected exactly as
it would be during onboarding.

CONSTRUCTOR-SHAPE GUARANTEE, enforced by
tests/test_preferences_service_isolation.py: this class builds a
UserRepository and nothing else. No CVRepository, no ProfileRepository,
ever in scope. That test parses this file's own imports with `ast` and
fails if it names anything under app.db.repositories.cv,
app.db.repositories.profile, app.services.cv_intake,
app.services.cv_extraction, app.services.cv_embedding, or
app.integrations (Adzuna and Gemini both live there). It checks this
file's own import statements, not the transitive closure -- importing
three pure symbols from app.services.onboarding below does not import
anything CV-related INTO this file's own namespace, and nothing in
this class calls back into onboarding's CV-touching methods.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user import OnboardingState, PendingPreferenceField, User
from app.db.repositories.user import UserRepository
from app.services.onboarding import (
    EXPERIENCE_CHOICES,
    THRESHOLD_CHOICES,
    parse_list_input,
)
from app.services.replies import BotReply, Button

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = "pref"

_NOT_ONBOARDED_REPLY = BotReply(
    text=(
        "Let's finish setting you up first — send /start to carry on "
        "where you left off."
    )
)

_UNKNOWN_USER_REPLY = BotReply(text="I don't know you yet — send /start to begin.")

_EXPIRED_REPLY = BotReply(text="That button has expired. Send /preferences to try again.")


class PreferencesEditOutcome:
    """What handling one preferences interaction produced.

    Same shape and same reason as onboarding's DocumentOutcome and
    CallbackOutcome: `answered` is True only when a value was actually
    saved, so the handler can decide whether to echo a tapped choice
    onto the message it answered without needing to parse `reply.text`
    to find out.
    """

    __slots__ = ("reply", "answered")

    def __init__(self, reply: BotReply, answered: bool = False) -> None:
        self.reply = reply
        self.answered = answered


class PreferencesService:
    """Edit target_roles, preferred_locations, experience, or threshold.

    Only ever reachable from a COMPLETE user -- see start_menu. That
    guard is what keeps pending_preference_field from ever being set
    while onboarding_state is mid-flow; see the column's own docstring
    on app.db.models.user.User for the full argument.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._users = UserRepository(session)

    # -- entry point --------------------------------------------------

    async def start_menu(self, telegram_id: int) -> BotReply:
        """Handle /preferences: show the menu, or refuse if unfinished.

        Refusing here is what makes the ambiguous case impossible
        rather than merely unlikely: pending_preference_field is set
        nowhere else, so it can only ever become non-null while
        onboarding_state is COMPLETE.
        """
        user = await self._users.get_by_telegram_id(telegram_id)
        if user is None:
            return _UNKNOWN_USER_REPLY

        if OnboardingState(user.onboarding_state) is not OnboardingState.COMPLETE:
            return _NOT_ONBOARDED_REPLY

        return BotReply(
            text="Which would you like to change?",
            buttons=[
                [
                    Button("Target roles", f"{CALLBACK_PREFIX}:roles:menu"),
                    Button("Locations", f"{CALLBACK_PREFIX}:locations:menu"),
                ],
                [
                    Button("Experience", f"{CALLBACK_PREFIX}:experience:menu"),
                    Button("Alert threshold", f"{CALLBACK_PREFIX}:threshold:menu"),
                ],
            ],
        )

    # -- callback taps --------------------------------------------------

    async def handle_callback(
        self, telegram_id: int, data: str
    ) -> PreferencesEditOutcome:
        """Route a "pref:<field>:<value>" button tap.

        Re-checks onboarding_state on every tap, the same defensive
        reason onboarding's own handle_callback re-reads state instead
        of trusting the button: old messages stay tappable forever, and
        a /restart between the menu being sent and a button being
        pressed must not let a stale tap reach a field write.
        """
        user = await self._users.get_by_telegram_id(telegram_id)
        if user is None:
            return PreferencesEditOutcome(reply=_UNKNOWN_USER_REPLY)

        if OnboardingState(user.onboarding_state) is not OnboardingState.COMPLETE:
            return PreferencesEditOutcome(reply=_NOT_ONBOARDED_REPLY)

        parts = data.split(":")
        if len(parts) != 3 or parts[0] != CALLBACK_PREFIX:
            logger.warning("Unrecognised preferences callback data: %s", data)
            return PreferencesEditOutcome(reply=_EXPIRED_REPLY)

        _, field, value = parts

        if field == "roles":
            return await self._start_free_text(user, PendingPreferenceField.ROLES)
        if field == "locations":
            return await self._start_free_text(user, PendingPreferenceField.LOCATIONS)
        if field == "experience":
            return await self._handle_experience(user, value)
        if field == "threshold":
            return await self._handle_threshold(user, value)

        logger.warning("Unrecognised preferences field: %s", field)
        return PreferencesEditOutcome(reply=_EXPIRED_REPLY)

    async def _start_free_text(
        self, user: User, field: PendingPreferenceField
    ) -> PreferencesEditOutcome:
        """Menu tap for roles or locations: ask, and remember what for.

        The only place pending_preference_field is ever set to
        non-None.
        """
        await self._users.set_pending_preference_field(user, field)

        if field is PendingPreferenceField.ROLES:
            prompt = (
                "What roles should I look for instead?\n"
                "Send them separated by commas — for example:\n"
                "Backend Engineer, ML Engineer, Data Scientist"
            )
        else:
            prompt = (
                "Which locations work for you now?\n"
                "For example: Delhi, Noida, Bangalore"
            )

        return PreferencesEditOutcome(reply=BotReply(text=prompt))

    async def _handle_experience(
        self, user: User, value: str
    ) -> PreferencesEditOutcome:
        if value == "menu":
            return PreferencesEditOutcome(
                reply=BotReply(
                    text="How many years of experience do you have?",
                    buttons=[
                        [
                            Button(label, f"{CALLBACK_PREFIX}:experience:{label}")
                            for label in ("0-1", "1-3", "3-5")
                        ],
                        [
                            Button(label, f"{CALLBACK_PREFIX}:experience:{label}")
                            for label in ("5-8", "8+")
                        ],
                    ],
                )
            )

        bracket = EXPERIENCE_CHOICES.get(value)
        if bracket is None:
            return PreferencesEditOutcome(reply=_EXPIRED_REPLY)

        minimum, maximum = bracket
        preferences = await self._users.get_or_create_preferences(user.id)
        preferences.min_experience_years = minimum
        preferences.max_experience_years = maximum
        await self._session.flush()

        return PreferencesEditOutcome(
            reply=BotReply(text=f"✅ Updated: {value} years of experience."),
            answered=True,
        )

    async def _handle_threshold(
        self, user: User, value: str
    ) -> PreferencesEditOutcome:
        if value == "menu":
            return PreferencesEditOutcome(
                reply=BotReply(
                    text=(
                        "How selective should I be?\n\n"
                        "I only message you about jobs scoring above this.\n"
                        "0.6 — more jobs, looser matches\n"
                        "0.7 — balanced\n"
                        "0.8 — fewer jobs, stronger matches"
                    ),
                    buttons=[
                        [
                            Button(label, f"{CALLBACK_PREFIX}:threshold:{label}")
                            for label in THRESHOLD_CHOICES
                        ]
                    ],
                )
            )

        threshold = THRESHOLD_CHOICES.get(value)
        if threshold is None:
            return PreferencesEditOutcome(reply=_EXPIRED_REPLY)

        preferences = await self._users.get_or_create_preferences(user.id)
        preferences.notification_threshold = threshold
        await self._session.flush()

        return PreferencesEditOutcome(
            reply=BotReply(text=f"✅ Updated: alert threshold {threshold:.2f}."),
            answered=True,
        )

    # -- free-text answers ------------------------------------------------

    async def handle_text(self, telegram_id: int, text: str) -> BotReply:
        """Save a free-text answer for whichever field is pending.

        Only called by the router when it has already confirmed
        pending_preference_field is set for this user -- but re-reads
        the user row itself rather than trusting the caller, since a
        service should not depend on a caller having checked what it
        can check itself.
        """
        user = await self._users.get_by_telegram_id(telegram_id)
        if user is None:
            return _UNKNOWN_USER_REPLY

        # Wrapped into the enum once and compared by identity from here
        # on -- the same convention OnboardingState uses throughout this
        # file's sibling, onboarding.py. A raw string is only ever read
        # or written at this one boundary.
        raw_field = user.pending_preference_field
        field = PendingPreferenceField(raw_field) if raw_field is not None else None
        preferences = await self._users.get_or_create_preferences(user.id)

        if field is PendingPreferenceField.ROLES:
            roles = parse_list_input(text)
            if not roles:
                return BotReply(
                    text="I didn't catch any roles there. Try: "
                    "Backend Engineer, ML Engineer"
                )
            preferences.target_roles = roles
            await self._users.set_pending_preference_field(user, None)
            await self._session.flush()
            return BotReply(text=f"✅ Updated target roles: {', '.join(roles)}.")

        if field is PendingPreferenceField.LOCATIONS:
            locations = parse_list_input(text)
            if not locations:
                return BotReply(
                    text="I didn't catch any locations. Try: Delhi, Noida, Gurgaon"
                )
            preferences.preferred_locations = locations
            await self._users.set_pending_preference_field(user, None)
            await self._session.flush()
            return BotReply(text=f"✅ Updated locations: {', '.join(locations)}.")

        # Not actually reachable through the router's own contract --
        # it only calls here when pending_preference_field is set --
        # but this service does not trust that and must say something
        # sane if it is ever called anyway.
        return BotReply(text="Nothing is pending. Send /preferences to change something.")
