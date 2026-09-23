"""Day 16 fix: score_and_notify_user must never fire from a mid-onboarding
user, whichever of extraction or onboarding-completion happens first.

Day 15's own live verification (CLAUDE.md, "Verified live, 2026-09-18")
called _try_instant_recommendation directly against a user whose
preferences were already filled in -- it copied a real, complete
profile's embedding onto a synthetic test user. That never exercised a
user racing ahead of their own extraction, which is exactly the
ordering this file drives for real: through OnboardingService's actual
state-machine methods (same technique as
tests/integration/test_onboarding_callback_echo.py), not hand-inserted
rows standing in for what onboarding would have done.

embed_cv_version is monkeypatched at the call site
(app.bot.handlers.onboarding.embed_cv_version) rather than given a fake
client: _try_instant_recommendation calls it with no client override at
all, unlike embed_cv_version's own dedicated tests in
test_cv_embedding_pipeline.py, so there is no other seam to inject
through without changing production code to suit a test.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

import app.bot.handlers.onboarding as onboarding_handler
import app.services.notification_delivery as notification_delivery
from app.bot.handlers.onboarding import _try_instant_recommendation
from app.db.models.cv import CV, CVVersion, ExtractionStatus
from app.db.models.job import Job
from app.db.models.profile import Profile
from app.db.models.recommendation import (
    Notification,
    NotificationStatus,
    Recommendation,
    TRIGGER_SOURCE_ONBOARDING,
)
from app.db.models.user import OnboardingState, User, UserPreference
from app.db.session import session_scope
from app.integrations.telegram import SendResult
from app.services.cv_embedding import CVEmbeddingOutcome
from app.services.notification_delivery import score_and_notify_user
from app.services.onboarding import OnboardingService

_VECTOR = [0.1] * 768
_REALISTIC_PROFILE = {
    "current_title": "ML Engineer",
    "skills": ["Python"],
    "summary": "Builds ML systems end to end.",
}


class _CountingNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send(self, *, chat_id, reply):
        self.sent.append((chat_id, reply.text))
        return SendResult(ok=True)

    async def __aenter__(self) -> "_CountingNotifier":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None


def _patch_notifier_construction(monkeypatch) -> list[tuple[int, str]]:
    """Patches the TelegramNotifier CLASS itself (not passed as a
    parameter), for a caller like _try_instant_recommendation that has
    no notifier argument at all and so always reaches
    deliver_notifications()'s `TelegramNotifier()` default
    (notification_delivery.py:434). tests/integration/conftest.py's
    autouse fixture already replaces that default with one that RAISES,
    specifically so this can't be forgotten -- this overrides it again,
    on top, with one that records instead. Returns the list every
    send() call appends to.
    """
    sent: list[tuple[int, str]] = []

    class _ConstructionSiteNotifier:
        async def send(self, *, chat_id, reply):
            sent.append((chat_id, reply.text))
            return SendResult(ok=True)

        async def __aenter__(self) -> "_ConstructionSiteNotifier":
            return self

        async def __aexit__(self, *exc_info: object) -> None:
            return None

    monkeypatch.setattr(
        notification_delivery, "TelegramNotifier", _ConstructionSiteNotifier
    )
    return sent


async def _fake_embed_cv_version(version_id: int, client=None) -> CVEmbeddingOutcome:
    """Sets a real embedding directly -- no Gemini call, no network."""
    async with session_scope() as session:
        version = await session.get(CVVersion, version_id)
        version.embedding = _VECTOR
        version.embedding_model = "test"
        version.embedded_at = datetime.now(timezone.utc)
    return CVEmbeddingOutcome(embedded=True)


async def _build_user_awaiting_roles(*, telegram_id: int) -> tuple[int, int]:
    """A user right after their first CV upload: an UNEMBEDDED CV
    version and a Profile exist (as cv_extraction.py would have written
    them), onboarding_state is AWAITING_ROLES, and -- deliberately --
    there is no UserPreference row yet. Returns (user_id, version_id).
    """
    async with session_scope() as session:
        user = User(
            telegram_id=telegram_id,
            full_name="Test Candidate",
            onboarding_state=OnboardingState.AWAITING_ROLES.value,
        )
        session.add(user)
        await session.flush()

        cv = CV(
            user_id=user.id,
            file_name="cv.pdf",
            file_type="pdf",
            storage_path="unused",
            extraction_status=ExtractionStatus.COMPLETE.value,
        )
        session.add(cv)
        await session.flush()

        version = CVVersion(
            cv_id=cv.id, version=1, extracted_profile=_REALISTIC_PROFILE
        )
        session.add(version)
        await session.flush()

        session.add(
            Profile(
                user_id=user.id,
                current_title="ML Engineer",
                location="Bangalore",
                total_experience_years=5.0,
                skills=[],
                active_cv_version_id=version.id,
            )
        )

        return user.id, version.id


async def _advance_through_preferences_to_threshold(
    session, telegram_id: int, *, roles_text: str, locations_text: str
) -> None:
    """Walks AWAITING_ROLES -> AWAITING_THRESHOLD via OnboardingService's
    own real methods, so target_roles/preferred_locations end up
    genuinely answered rather than hand-inserted -- same technique as
    test_onboarding_callback_echo.py's _advance_to_awaiting_remote."""
    service = OnboardingService(session)
    user = await service._users.get_by_telegram_id(telegram_id)
    await service._save_roles(user, roles_text)
    user = await service._users.get_by_telegram_id(telegram_id)
    await service._save_locations(user, locations_text)
    user = await service._users.get_by_telegram_id(telegram_id)
    await service._save_remote(user, False)
    user = await service._users.get_by_telegram_id(telegram_id)
    await service._save_experience(user, "3-5")
    # Now at AWAITING_THRESHOLD.


async def _complete_threshold(session, telegram_id: int):
    return await OnboardingService(session).handle_callback(
        telegram_id=telegram_id, data="onb:threshold:0.6"
    )


async def _build_matching_job(*, external_id: str) -> None:
    async with session_scope() as session:
        session.add(
            Job(
                source="test",
                external_id=external_id,
                title="ML Engineer",
                company="Acme Corp",
                location="Remote",
                description="Build things.",
                url="https://example.invalid/" + external_id,
                work_mode="remote",
                min_experience_years=3,
                max_experience_years=8,
                is_active=True,
                is_excluded=False,
                embedding=_VECTOR,
                embedding_model="test",
                content_hash="test-hash-" + external_id,
            )
        )


async def _recommendation_for(user_id: int) -> Recommendation | None:
    async with session_scope() as session:
        return (
            await session.execute(
                select(Recommendation).where(Recommendation.user_id == user_id)
            )
        ).scalar_one_or_none()


async def _sent_notifications_for(user_id: int) -> list[Notification]:
    async with session_scope() as session:
        return (
            (
                await session.execute(
                    select(Notification).where(
                        Notification.user_id == user_id,
                        Notification.status == NotificationStatus.SENT,
                    )
                )
            )
            .scalars()
            .all()
        )


# --- (a) extraction finishes before onboarding completes -----------------


def test_extraction_first_does_not_score_completion_scores_exactly_once(
    run_with_database: Callable[[Callable[[], Awaitable[Any]]], Any],
    monkeypatch,
) -> None:
    monkeypatch.setattr(onboarding_handler, "embed_cv_version", _fake_embed_cv_version)

    async def body() -> dict[str, Any]:
        user_id, version_id = await _build_user_awaiting_roles(telegram_id=940001)
        await _build_matching_job(external_id="day16-order-a")

        # Extraction finishes first: the background task runs while the
        # user is still sitting at AWAITING_ROLES.
        await _try_instant_recommendation(user_id, version_id)

        no_score_yet = await _recommendation_for(user_id)

        # The user now answers everything else, for real, and taps
        # the final threshold button.
        async with session_scope() as session:
            await _advance_through_preferences_to_threshold(
                session,
                940001,
                roles_text="ML Engineer",
                locations_text="Bangalore",
            )

        async with session_scope() as session:
            outcome = await _complete_threshold(session, 940001)

        notifier = _CountingNotifier()
        await score_and_notify_user(
            outcome.user_id,
            trigger_source=TRIGGER_SOURCE_ONBOARDING,
            rescore=True,
            notifier=notifier,
        )

        recommendation = await _recommendation_for(user_id)
        sent = await _sent_notifications_for(user_id)

        return {
            "no_score_yet": no_score_yet,
            "outcome": outcome,
            "recommendation": recommendation,
            "sent": sent,
        }

    observed = run_with_database(body)

    assert observed["no_score_yet"] is None, (
        "the extraction path scored while onboarding was still mid-flow"
    )
    assert observed["outcome"].recommendation_trigger == "rescore"
    assert observed["outcome"].user_id is not None

    recommendation = observed["recommendation"]
    assert recommendation is not None, "completion should have triggered exactly one run"
    assert recommendation.title_score == 1.0, (
        "title_score is not 1.0 -- the run used empty/default target_roles "
        "instead of the real ones just answered"
    )
    assert len(observed["sent"]) == 1


# --- (b) onboarding completes before extraction ---------------------------


def test_completion_first_scores_nothing_extraction_scores_exactly_once(
    run_with_database: Callable[[Callable[[], Awaitable[Any]]], Any],
    monkeypatch,
) -> None:
    monkeypatch.setattr(onboarding_handler, "embed_cv_version", _fake_embed_cv_version)

    async def body() -> dict[str, Any]:
        user_id, version_id = await _build_user_awaiting_roles(telegram_id=940002)
        await _build_matching_job(external_id="day16-order-b")

        # The user finishes every preference question, for real, before
        # extraction's own background task has embedded anything.
        async with session_scope() as session:
            await _advance_through_preferences_to_threshold(
                session,
                940002,
                roles_text="ML Engineer",
                locations_text="Bangalore",
            )

        async with session_scope() as session:
            outcome = await _complete_threshold(session, 940002)

        notifier = _CountingNotifier()
        completion_result = await score_and_notify_user(
            outcome.user_id,
            trigger_source=TRIGGER_SOURCE_ONBOARDING,
            rescore=True,
            notifier=notifier,
        )

        no_score_yet = await _recommendation_for(user_id)

        # Extraction's background task finally runs.
        # _try_instant_recommendation has no notifier parameter at all,
        # so the real TelegramNotifier() construction site is patched
        # instead of passing a value -- see _patch_notifier_construction.
        patched_sent = _patch_notifier_construction(monkeypatch)
        await _try_instant_recommendation(user_id, version_id)

        recommendation = await _recommendation_for(user_id)
        sent = await _sent_notifications_for(user_id)

        return {
            "completion_result": completion_result,
            "no_score_yet": no_score_yet,
            "recommendation": recommendation,
            "sent": sent,
            "patched_sent": patched_sent,
        }

    observed = run_with_database(body)

    assert observed["no_score_yet"] is None, (
        "the completion trigger scored a user whose CV was not embedded yet"
    )
    assert observed["completion_result"]["sent"] == 0

    recommendation = observed["recommendation"]
    assert recommendation is not None, "extraction should have triggered exactly one run"
    assert recommendation.title_score == 1.0
    assert len(observed["sent"]) == 1
    assert len(observed["patched_sent"]) == 1


# --- (c) /update_cv for a COMPLETE user ------------------------------------


def test_update_cv_for_a_complete_user_still_triggers_the_instant_path(
    run_with_database: Callable[[Callable[[], Awaitable[Any]]], Any],
    monkeypatch,
) -> None:
    monkeypatch.setattr(onboarding_handler, "embed_cv_version", _fake_embed_cv_version)

    async def body() -> dict[str, Any]:
        async with session_scope() as session:
            user = User(
                telegram_id=940003,
                full_name="Returning Candidate",
                onboarding_state=OnboardingState.COMPLETE.value,
            )
            session.add(user)
            await session.flush()
            user_id = user.id

            cv = CV(
                user_id=user_id,
                file_name="old_cv.pdf",
                file_type="pdf",
                storage_path="unused",
                extraction_status=ExtractionStatus.COMPLETE.value,
            )
            session.add(cv)
            await session.flush()

            old_version = CVVersion(
                cv_id=cv.id,
                version=1,
                extracted_profile=_REALISTIC_PROFILE,
                embedding=_VECTOR,
                embedding_model="test",
                embedded_at=datetime.now(timezone.utc),
            )
            session.add(old_version)
            await session.flush()

            session.add(
                Profile(
                    user_id=user_id,
                    current_title="ML Engineer",
                    location="Bangalore",
                    total_experience_years=5.0,
                    skills=[],
                    active_cv_version_id=old_version.id,
                )
            )
            session.add(
                UserPreference(
                    user_id=user_id,
                    target_roles=["ML Engineer"],
                    preferred_locations=["Bangalore"],
                    notification_threshold=0.5,
                )
            )

            # /update_cv: a new, not-yet-embedded version, already made
            # the profile's active version -- exactly what
            # cv_extraction.extract_cv's phase 3 does before
            # _try_instant_recommendation is ever called.
            new_version = CVVersion(
                cv_id=cv.id, version=2, extracted_profile=_REALISTIC_PROFILE
            )
            session.add(new_version)
            await session.flush()

            profile = (
                await session.execute(
                    select(Profile).where(Profile.user_id == user_id)
                )
            ).scalar_one()
            profile.active_cv_version_id = new_version.id

            new_version_id = new_version.id

        await _build_matching_job(external_id="day16-order-c")

        # _try_instant_recommendation has no notifier parameter at all,
        # so the real TelegramNotifier() construction site is patched
        # instead of passing a value -- see _patch_notifier_construction.
        patched_sent = _patch_notifier_construction(monkeypatch)
        await _try_instant_recommendation(user_id, new_version_id)

        recommendation = await _recommendation_for(user_id)
        sent = await _sent_notifications_for(user_id)
        return {"recommendation": recommendation, "sent": sent, "patched_sent": patched_sent}

    observed = run_with_database(body)

    assert observed["recommendation"] is not None
    assert len(observed["sent"]) == 1
    assert len(observed["patched_sent"]) == 1


# --- (d) no run ever scores a user with no UserPreference row ------------


def test_completing_onboarding_always_creates_a_preferences_row_first(
    run_with_database: Callable[[Callable[[], Awaitable[Any]]], Any],
) -> None:
    """The completion trigger only ever fires from _save_threshold's
    success branch, and _save_threshold calls get_or_create_preferences
    (app/services/onboarding.py) before flipping onboarding_state to
    COMPLETE. This is what makes "COMPLETE but no preferences row"
    unreachable through the real flow -- proven here by checking a row
    exists at the exact moment the trigger fires, not assumed."""

    async def body() -> dict[str, Any]:
        user_id, _version_id = await _build_user_awaiting_roles(telegram_id=940004)

        async with session_scope() as session:
            preferences_before = (
                await session.execute(
                    select(UserPreference).where(UserPreference.user_id == user_id)
                )
            ).scalar_one_or_none()

        async with session_scope() as session:
            await _advance_through_preferences_to_threshold(
                session, 940004, roles_text="ML Engineer", locations_text="Bangalore"
            )

        async with session_scope() as session:
            outcome = await _complete_threshold(session, 940004)

        async with session_scope() as session:
            preferences_after = (
                await session.execute(
                    select(UserPreference).where(UserPreference.user_id == user_id)
                )
            ).scalar_one_or_none()

        return {
            "preferences_before": preferences_before,
            "outcome": outcome,
            "preferences_after": preferences_after,
        }

    observed = run_with_database(body)

    assert observed["preferences_before"] is None, (
        "test setup is wrong: a preferences row already existed before "
        "onboarding ever asked for one"
    )
    assert observed["outcome"].recommendation_trigger == "rescore"
    assert observed["preferences_after"] is not None, (
        "the completion trigger fired without a UserPreference row existing"
    )
    assert observed["preferences_after"].target_roles == ["ML Engineer"]
