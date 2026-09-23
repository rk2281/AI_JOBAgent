"""score_and_notify_user against a real database -- Day 16.

Proves what tests/test_score_and_notify_user.py's pure state-machine
tests cannot, because they fake run_scoring and run_notification_
delivery entirely: that coalescing two triggers into two SEQUENTIAL
runs (never concurrent, because of the in-flight guard) still yields at
most one real send per (user, job) -- via the REAL scoring signals, the
REAL gate, and the REAL notifications table's partial unique index --
and that the coalesced rerun picks up whatever preferences were most
recently committed, not whatever they were when the first trigger fired.

Real PostgreSQL, a fake Telegram notifier. Mirrors the shape of
tests/integration/test_cv_embedding_pipeline.py: the only substitution
is the paid/network call, which here is Telegram, not Gemini -- nothing
in this file's own scoring path touches Gemini at all (run_scoring reads
already-stored embeddings; see test_score_and_notify_user.py's structural
note on that).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.db.models.cv import CV, CVVersion, ExtractionStatus
from app.db.models.job import Job
from app.db.models.profile import Profile
from app.db.models.recommendation import (
    Notification,
    NotificationStatus,
    Recommendation,
    TRIGGER_SOURCE_ONBOARDING,
    TRIGGER_SOURCE_PREFERENCES,
)
from app.db.models.user import User, UserPreference
from app.db.session import session_scope
from app.integrations.telegram import SendResult
import app.services.notification_delivery as notification_delivery
from app.services.notification_delivery import score_and_notify_user

_VECTOR = [0.1] * 768


class _CountingNotifier:
    """Records every send; never touches a network or a bot token."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send(self, *, chat_id, reply):
        self.sent.append((chat_id, reply.text))
        return SendResult(ok=True)

    async def __aenter__(self) -> "_CountingNotifier":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None


def _reset_in_flight_state() -> None:
    """The in-flight/rerun sets are process-global, not per-test. Each
    test below uses its own telegram_id/user, so a leaked entry from a
    failed run cannot poison a later one either."""
    notification_delivery._in_flight_users.clear()
    notification_delivery._rerun_needed_users.clear()


async def _build_scorable_user(*, telegram_id: int) -> int:
    """User + CV + embedded CVVersion + Profile + UserPreference, shaped
    to clear all three notification gates against the job
    _build_matching_job builds below. Returns the internal user id."""
    async with session_scope() as session:
        user = User(telegram_id=telegram_id, full_name="Test Candidate", is_active=True)
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
            cv_id=cv.id,
            version=1,
            extracted_profile={},
            embedding=_VECTOR,
            embedding_model="test",
            embedded_at=datetime.now(timezone.utc),
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

        session.add(
            UserPreference(
                user_id=user.id,
                target_roles=["ML Engineer"],
                preferred_locations=["Bangalore"],
                notification_threshold=0.5,
            )
        )

        return user.id


async def _build_matching_job(*, external_id: str) -> int:
    """A job engineered to clear the gate against the profile above:
    remote (score_location returns 1.0 unconditionally for a remote
    role, regardless of preferred_locations), a 3-8 year range covering
    the candidate's 5, and a title matching "ML Engineer" once weak
    tokens are stripped. No job_skills rows -- the skill signal
    abstains, which is fine: semantic + experience + location + title
    already covers enough of the weight to clear
    min_weight_covered_to_notify without it.
    """
    async with session_scope() as session:
        job = Job(
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
        session.add(job)
        await session.flush()
        return job.id


def test_a_lone_trigger_sends_once_with_the_right_trigger_source(
    run_with_database: Callable[[Callable[[], Awaitable[Any]]], Any],
) -> None:
    _reset_in_flight_state()

    async def body() -> dict[str, Any]:
        user_id = await _build_scorable_user(telegram_id=920001)
        await _build_matching_job(external_id="day16-lone")
        notifier = _CountingNotifier()

        await score_and_notify_user(
            user_id,
            trigger_source=TRIGGER_SOURCE_PREFERENCES,
            rescore=True,
            notifier=notifier,
        )

        async with session_scope() as session:
            notifications = (
                (await session.execute(select(Notification).where(Notification.user_id == user_id)))
                .scalars()
                .all()
            )

        return {"notifications": notifications, "sent": notifier.sent}

    observed = run_with_database(body)

    assert len(observed["sent"]) == 1
    assert len(observed["notifications"]) == 1
    row = observed["notifications"][0]
    assert row.status == NotificationStatus.SENT
    assert row.trigger_source == TRIGGER_SOURCE_PREFERENCES


def test_two_overlapping_triggers_coalesce_and_still_send_at_most_once(
    run_with_database: Callable[[Callable[[], Awaitable[Any]]], Any],
    monkeypatch,
) -> None:
    """Simulates an onboarding-triggered run and a preferences-triggered
    run landing for the same user while the first is still in flight --
    exactly the case Day 16's review asked to be covered. run_scoring is
    WRAPPED, not replaced: the real scoring and delivery code still runs
    against the real database. Only its start is paused, deterministically,
    to force the two calls to overlap instead of racing on timing."""
    _reset_in_flight_state()

    real_run_scoring = notification_delivery.run_scoring
    run_started = asyncio.Event()
    release_first_run = asyncio.Event()
    score_calls = 0

    async def wrapped_run_scoring(*, user_id: int):
        nonlocal score_calls
        score_calls += 1
        if score_calls == 1:
            run_started.set()
            await release_first_run.wait()
        return await real_run_scoring(user_id=user_id)

    monkeypatch.setattr(notification_delivery, "run_scoring", wrapped_run_scoring)

    async def body() -> dict[str, Any]:
        user_id = await _build_scorable_user(telegram_id=920002)
        await _build_matching_job(external_id="day16-coalesce")
        notifier = _CountingNotifier()

        first = asyncio.create_task(
            score_and_notify_user(
                user_id,
                trigger_source=TRIGGER_SOURCE_ONBOARDING,
                rescore=True,
                notifier=notifier,
            )
        )
        await run_started.wait()

        # The coalesced trigger: a threshold-only edit, so rescore=False
        # -- the rerun must upgrade this to True on its own (covered
        # directly in tests/test_score_and_notify_user.py).
        second = await score_and_notify_user(
            user_id,
            trigger_source=TRIGGER_SOURCE_PREFERENCES,
            rescore=False,
            notifier=notifier,
        )
        assert second["status"] == "coalesced"

        release_first_run.set()
        await first

        async with session_scope() as session:
            notifications = (
                (await session.execute(select(Notification).where(Notification.user_id == user_id)))
                .scalars()
                .all()
            )

        return {"notifications": notifications, "sent": notifier.sent}

    observed = run_with_database(body)

    assert score_calls == 2, f"expected exactly two scoring passes, got {score_calls}"
    assert len(observed["sent"]) == 1, "at most one real Telegram send for this (user, job) pair"
    sent_rows = [n for n in observed["notifications"] if n.status == NotificationStatus.SENT]
    assert len(sent_rows) == 1


def test_the_coalesced_rerun_sees_preferences_committed_after_the_first_run_started(
    run_with_database: Callable[[Callable[[], Awaitable[Any]]], Any],
    monkeypatch,
) -> None:
    """"the second seeing the final preferences" -- Day 16's own wording.

    target_roles is edited to something with no token overlap with the
    job's title WHILE the first run is paused inside run_scoring, then a
    second trigger is issued before releasing it. If the coalesced rerun
    re-reads preferences (correct), the stored title_score ends up 0.0 --
    a real miss, not an abstain, since both sides still have tokens. If
    it replayed whatever the first run already had in memory (wrong),
    title_score would still read 1.0.
    """
    _reset_in_flight_state()

    real_run_scoring = notification_delivery.run_scoring
    run_started = asyncio.Event()
    release_first_run = asyncio.Event()
    score_calls = 0

    async def wrapped_run_scoring(*, user_id: int):
        nonlocal score_calls
        score_calls += 1
        if score_calls == 1:
            run_started.set()
            await release_first_run.wait()
        return await real_run_scoring(user_id=user_id)

    monkeypatch.setattr(notification_delivery, "run_scoring", wrapped_run_scoring)

    async def body() -> dict[str, Any]:
        user_id = await _build_scorable_user(telegram_id=920003)
        await _build_matching_job(external_id="day16-fresh-prefs")

        first = asyncio.create_task(
            score_and_notify_user(
                user_id, trigger_source=TRIGGER_SOURCE_ONBOARDING, rescore=True
            )
        )
        await run_started.wait()

        async with session_scope() as session:
            preferences = (
                await session.execute(
                    select(UserPreference).where(UserPreference.user_id == user_id)
                )
            ).scalar_one()
            preferences.target_roles = ["Totally Different Role"]

        await score_and_notify_user(
            user_id, trigger_source=TRIGGER_SOURCE_PREFERENCES, rescore=True
        )

        release_first_run.set()
        await first

        async with session_scope() as session:
            recommendation = (
                await session.execute(
                    select(Recommendation).where(Recommendation.user_id == user_id)
                )
            ).scalar_one()

        return recommendation

    recommendation = run_with_database(body)

    assert score_calls == 2
    assert recommendation.title_score == 0.0, (
        "title_score still reflects the ORIGINAL target_roles -- the "
        "coalesced rerun did not re-read the edited preferences"
    )
