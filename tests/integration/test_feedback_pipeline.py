"""Day 12 integration test 5 -- a button tap -> user_feedback rows.

    callback_data "fb:saved:42"                (what Telegram delivers)
        -> FeedbackService.handle_callback     (REAL)
        -> FeedbackRepository.record           (REAL ON CONFLICT)
        -> user_feedback rows                  (REAL PostgreSQL)

The string is the real wire format, built by the same
`app.services.notification_message` code that puts it on the button, so
this is not a test agreeing with itself about a format.

WHAT THIS DOES NOT COVER

The tap itself. `app/bot/handlers/feedback.py` unwraps a
`telegram.Update` and calls the service; that unwrapping is covered by
`tests/test_callback_routing.py`, and the round trip through Telegram's
servers is NOT VERIFIED anywhere -- see docs/TEST_RESULTS.md.

The behaviour under test is the one the repository was built for and
that no unit test could reach: `uq_user_feedback_user_job_action` is a
three-column constraint enforced by PostgreSQL, and the difference
between "recorded" and "already recorded" is `RETURNING id` coming back
empty. Neither exists without a database.

Feedback rows on live data: 0. This is the first time the table has
been written to outside a fixture.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.db.models.job import Job
from app.db.models.recommendation import FeedbackAction, UserFeedback
from app.db.models.user import User
from app.db.session import session_scope
from app.services.feedback import FeedbackService
from app.services.notification_message import CALLBACK_PREFIX

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
TELEGRAM_ID = 930001


def callback_data(action: FeedbackAction, job_id: int) -> str:
    """The exact string the inline keyboard carries."""
    return f"{CALLBACK_PREFIX}:{action.value}:{job_id}"


async def _seed() -> int:
    async with session_scope() as session:
        session.add(User(telegram_id=TELEGRAM_ID, full_name="Priya Sharma"))
        job = Job(
            source="fixture",
            external_id="job-1",
            title="Machine Learning Engineer",
            company="Northwind Analytics",
            location="Delhi",
            url="https://example.test/1",
            posted_at=NOW - timedelta(days=1),
            last_seen_at=NOW,
            content_hash="hash-1",
        )
        session.add(job)
        await session.flush()
        return job.id


def test_a_tap_reaches_the_database_and_the_reply_says_so(
    run_with_database: Callable[[Callable[[], Awaitable[Any]]], Any],
) -> None:
    async def body() -> dict[str, Any]:
        job_id = await _seed()

        async with session_scope() as session:
            reply = await FeedbackService(session).handle_callback(
                TELEGRAM_ID, callback_data(FeedbackAction.SAVED, job_id)
            )

        async with session_scope() as session:
            rows = (await session.execute(select(UserFeedback))).scalars().all()
            return {
                "reply": reply.text,
                "rows": [(row.action, row.job_id, row.user_id) for row in rows],
            }

    observed = run_with_database(body)

    assert len(observed["rows"]) == 1
    action, job_id, user_id = observed["rows"][0]
    assert action is FeedbackAction.SAVED
    assert user_id == 1
    assert "Saved" in observed["reply"]


def test_the_same_button_twice_writes_one_row_and_is_acknowledged(
    run_with_database: Callable[[Callable[[], Awaitable[Any]]], Any],
) -> None:
    """An inline keyboard never expires, so a repeat tap is normal.

    The second tap must not error, must not write a second row, and
    must say something different from the first -- otherwise a user
    cannot tell that their tap did anything.
    """

    async def body() -> dict[str, Any]:
        job_id = await _seed()
        data = callback_data(FeedbackAction.INTERESTED, job_id)

        async with session_scope() as session:
            first = await FeedbackService(session).handle_callback(TELEGRAM_ID, data)
        async with session_scope() as session:
            second = await FeedbackService(session).handle_callback(TELEGRAM_ID, data)

        async with session_scope() as session:
            rows = (await session.execute(select(UserFeedback))).scalars().all()
            return {
                "first": first.text,
                "second": second.text,
                "row_count": len(rows),
            }

    observed = run_with_database(body)

    assert observed["row_count"] == 1
    assert observed["first"] != observed["second"]


def test_contradictory_feedback_is_kept_as_two_rows(
    run_with_database: Callable[[Callable[[], Awaitable[Any]]], Any],
) -> None:
    """Interested then Not Relevant is two facts, not one correction.

    The gap between "looked appealing in a notification" and "stopped
    looking appealing once read" is the signal a later re-ranking
    wants, and it exists only while both rows do. If this test starts
    failing because the second tap overwrote the first, the re-ranking
    input has been deleted -- quietly, and with the table still looking
    perfectly sensible.
    """

    async def body() -> dict[str, Any]:
        job_id = await _seed()

        async with session_scope() as session:
            service = FeedbackService(session)
            await service.handle_callback(
                TELEGRAM_ID, callback_data(FeedbackAction.INTERESTED, job_id)
            )
        async with session_scope() as session:
            reply = await FeedbackService(session).handle_callback(
                TELEGRAM_ID, callback_data(FeedbackAction.NOT_RELEVANT, job_id)
            )

        async with session_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(UserFeedback).order_by(UserFeedback.created_at)
                    )
                )
                .scalars()
                .all()
            )
            return {
                "actions": [row.action for row in rows],
                "reply": reply.text,
            }

    observed = run_with_database(body)

    assert observed["actions"] == [
        FeedbackAction.INTERESTED,
        FeedbackAction.NOT_RELEVANT,
    ]
    # The reply reports BOTH, rather than picking a winner.
    assert "Interested" in observed["reply"]
    assert "Not Relevant" in observed["reply"]


def test_a_tap_from_an_unknown_account_writes_nothing(
    run_with_database: Callable[[Callable[[], Awaitable[Any]]], Any],
) -> None:
    """Possible whenever the database is reset under a live message."""

    async def body() -> dict[str, Any]:
        job_id = await _seed()

        async with session_scope() as session:
            reply = await FeedbackService(session).handle_callback(
                999999, callback_data(FeedbackAction.SAVED, job_id)
            )

        async with session_scope() as session:
            rows = (await session.execute(select(UserFeedback))).scalars().all()
            return {"reply": reply.text, "row_count": len(rows)}

    observed = run_with_database(body)

    assert observed["row_count"] == 0
    assert "/start" in observed["reply"]


def test_a_callback_this_service_does_not_own_is_declined(
    run_with_database: Callable[[Callable[[], Awaitable[Any]]], Any],
) -> None:
    """callback_data comes back from Telegram's servers as input.

    We wrote it, but it round-tripped through somebody else's system
    before returning, so it is validated rather than trusted.
    """

    async def body() -> dict[str, Any]:
        await _seed()

        replies = []
        async with session_scope() as session:
            service = FeedbackService(session)
            for data in ("onb:role:2", "fb:teleport:1", "fb:saved:not-a-number", "fb:saved"):
                replies.append((await service.handle_callback(TELEGRAM_ID, data)).text)

        async with session_scope() as session:
            rows = (await session.execute(select(UserFeedback))).scalars().all()
            return {"replies": replies, "row_count": len(rows)}

    observed = run_with_database(body)

    assert observed["row_count"] == 0
    assert all("not one I recognise" in reply for reply in observed["replies"])
