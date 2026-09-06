"""CallbackOutcome.answered, against a real onboarding flow.

Added alongside the fix that makes a tapped onboarding button echo its
choice onto the question it answered (app/bot/handlers/onboarding.py).
The handler only echoes when `answered` is True -- echoing a choice
that was never saved would show the user something false -- so this
file is the thing that keeps that flag honest against a real database,
not just against hand-built fixtures.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.db.models.user import OnboardingState
from app.db.session import session_scope
from app.services.onboarding import CallbackOutcome, OnboardingService

TELEGRAM_ID = 700001


async def _advance_to_awaiting_remote(session) -> None:
    """Walk a fresh user to AWAITING_REMOTE the same way onboarding does."""
    service = OnboardingService(session)
    await service.start(TELEGRAM_ID, username=None, full_name="Test User")

    user = await service._users.get_by_telegram_id(TELEGRAM_ID)
    await service._users.set_onboarding_state(user, OnboardingState.AWAITING_ROLES)
    await service._save_roles(user, "Backend Engineer")
    # _save_roles already advances to AWAITING_LOCATIONS.
    user = await service._users.get_by_telegram_id(TELEGRAM_ID)
    await service._save_locations(user, "Delhi")
    # Now at AWAITING_REMOTE.


def test_a_valid_tap_for_the_current_step_is_answered(
    run_with_database: Callable[[Callable[[], Awaitable[Any]]], Any],
) -> None:
    async def body() -> CallbackOutcome:
        async with session_scope() as session:
            await _advance_to_awaiting_remote(session)

        async with session_scope() as session:
            return await OnboardingService(session).handle_callback(
                telegram_id=TELEGRAM_ID, data="onb:remote:yes"
            )

    outcome = run_with_database(body)

    assert outcome.answered is True


def test_a_tap_from_an_earlier_step_is_not_answered(
    run_with_database: Callable[[Callable[[], Awaitable[Any]]], Any],
) -> None:
    """User is at AWAITING_REMOTE; the tapped button was for AWAITING_EXPERIENCE.

    This is exactly "a user can scroll up a week later and press a
    button from a finished flow" -- the state check that already
    existed, now also asserted against the new flag.
    """

    async def body() -> CallbackOutcome:
        async with session_scope() as session:
            await _advance_to_awaiting_remote(session)

        async with session_scope() as session:
            return await OnboardingService(session).handle_callback(
                telegram_id=TELEGRAM_ID, data="onb:exp:0-1"
            )

    outcome = run_with_database(body)

    assert outcome.answered is False


def test_unrecognised_callback_data_is_not_answered(
    run_with_database: Callable[[Callable[[], Awaitable[Any]]], Any],
) -> None:
    async def body() -> CallbackOutcome:
        async with session_scope() as session:
            await _advance_to_awaiting_remote(session)

        async with session_scope() as session:
            return await OnboardingService(session).handle_callback(
                telegram_id=TELEGRAM_ID, data="not:the:right:shape:at:all"
            )

    outcome = run_with_database(body)

    assert outcome.answered is False


def test_a_value_outside_the_fixed_set_is_not_answered(
    run_with_database: Callable[[Callable[[], Awaitable[Any]]], Any],
) -> None:
    """The step matches, but the value could never have come from a real
    button -- EXPERIENCE_CHOICES has no "99-100" key. A real client can
    only send this by forging callback data, but the check exists for
    exactly that case."""

    async def body() -> CallbackOutcome:
        async with session_scope() as session:
            await _advance_to_awaiting_remote(session)
            user = await OnboardingService(session)._users.get_by_telegram_id(
                TELEGRAM_ID
            )
            await OnboardingService(session)._users.set_onboarding_state(
                user, OnboardingState.AWAITING_EXPERIENCE
            )

        async with session_scope() as session:
            return await OnboardingService(session).handle_callback(
                telegram_id=TELEGRAM_ID, data="onb:exp:99-100"
            )

    outcome = run_with_database(body)

    assert outcome.answered is False
