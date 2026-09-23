"""Decide whether a plain text message answers onboarding or /preferences.

A Telegram text message carries no callback_data to dispatch on, so
something has to remember which question it answers. onboarding_state
and User.pending_preference_field both persist "which question", and
they are meant to be orthogonal -- see the docstring on
pending_preference_field for the full argument. This module is the one
place that reads both, so neither OnboardingService nor
PreferencesService needs to import or know about the other: coupling
two services together to resolve a routing question would be a second
thing to keep in step for no reason a router doesn't already solve on
its own.

Deliberately a function, not a class: it holds no state itself, it only
reads state that already lives on the User row.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user import OnboardingState
from app.db.repositories.user import UserRepository
from app.services.onboarding import OnboardingService
from app.services.preferences import PreferencesService
from app.services.replies import BotReply


@dataclass(frozen=True)
class TextRouteOutcome:
    """What routing one text message produced, for the handler to act on.

    Day 16's addition, same shape and same reason as onboarding's
    DocumentOutcome: `reply` is what most callers want, and
    `recommendation_trigger`/`user_id` are set ONLY when this text
    turned out to answer a score-affecting /preferences question
    (target_roles or preferred_locations, via PreferencesService.
    handle_text) -- never when it answered an onboarding question.
    Before this, route_text returned a bare BotReply and dropped
    PreferencesEditOutcome's own signal at exactly this boundary.
    """

    reply: BotReply
    user_id: int | None = None
    recommendation_trigger: str | None = None


async def route_text(session: AsyncSession, telegram_id: int, text: str) -> TextRouteOutcome:
    """Send a plain-text message to whichever service owns it.

    THE TIE-BREAK, made explicit rather than left to which `if` comes
    first: onboarding_state always wins. pending_preference_field is
    consulted ONLY when onboarding_state is exactly COMPLETE.

    This is not a 50/50 call -- the two columns are not symmetric.
    /preferences is the only thing that ever sets
    pending_preference_field, and it refuses to do so unless
    onboarding_state is already COMPLETE, so in the ordinary case the
    tie never actually arises. It CAN arise once: /restart resets
    onboarding_state without knowing pending_preference_field exists
    (deliberately -- /restart is not touched by this feature), which
    can leave a stale pending_preference_field sitting next to a
    non-COMPLETE onboarding_state. Making onboarding_state the master
    switch here means that stale value is inert until the user reaches
    COMPLETE again -- at which point _save_threshold has already
    cleared it (see onboarding.py), so it can never reactivate against
    an unrelated later message either. Two boundaries, one rule.
    """
    users = UserRepository(session)
    user = await users.get_by_telegram_id(telegram_id)

    if user is None or OnboardingState(user.onboarding_state) is not OnboardingState.COMPLETE:
        # Onboarding wins: either there is no user yet, or onboarding
        # is not finished, and pending_preference_field -- whatever it
        # holds -- is not this router's concern until COMPLETE.
        reply = await OnboardingService(session).handle_text(telegram_id, text)
        return TextRouteOutcome(reply=reply)

    if user.pending_preference_field is not None:
        outcome = await PreferencesService(session).handle_text(telegram_id, text)
        return TextRouteOutcome(
            reply=outcome.reply,
            user_id=outcome.user_id,
            recommendation_trigger=outcome.recommendation_trigger,
        )

    reply = await OnboardingService(session).handle_text(telegram_id, text)
    return TextRouteOutcome(reply=reply)
