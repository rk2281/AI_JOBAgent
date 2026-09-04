"""Feedback: the callback parser, the wording, and the service's decisions.

No database. FeedbackService is driven with fake repositories, so what
is under test is which row it asks for and what it says afterwards --
not the ON CONFLICT, which a fake cannot prove and which
tests/test_notification_constraints.py exercises against real
PostgreSQL.

THE DISTINCTION RUNNING THROUGH THIS FILE

Same action twice is a repeat and writes one row. A DIFFERENT action is
a second opinion and writes a second row, even when it contradicts the
first. Collapsing those two cases is the single most likely way to get
this wrong, and it fails quietly: the user is told their Not Relevant
was recorded while nothing was written.
"""

from __future__ import annotations

import asyncio

from app.db.models.recommendation import FeedbackAction
from app.services.feedback import (
    FeedbackService,
    current_feedback_summary,
    parse_feedback_callback,
)


def _run(coro):
    return asyncio.run(coro)


# --- the parser ----------------------------------------------------------


def test_every_feedback_action_round_trips_through_the_callback_data() -> None:
    """Driven off the enum rather than a list written out here, so an
    action added later without a button is a failure rather than a gap."""
    from app.services.notification_message import build_feedback_callback

    for action in FeedbackAction:
        assert parse_feedback_callback(build_feedback_callback(action, 42)) == (
            action,
            42,
        )


def test_an_onboarding_callback_is_not_parsed_as_feedback() -> None:
    """The bug this whole prefix scheme exists to prevent, asserted from
    the feedback side."""
    assert parse_feedback_callback("onb:remote:yes") is None
    assert parse_feedback_callback("onb:threshold:0.7") is None


def test_malformed_callback_data_returns_none_rather_than_raising() -> None:
    """callback_data is round-tripped through Telegram's servers and
    comes back as whatever was in the message, so it is input from
    outside the process even though this process wrote it."""
    for data in (
        "",
        "fb",
        "fb:interested",
        "fb:interested:42:extra",
        "fb:interested:not-a-number",
        "fb:no_such_action:42",
        "garbage",
    ):
        assert parse_feedback_callback(data) is None


def test_a_negative_or_large_job_id_still_parses() -> None:
    """int() is the validation, not a range check. A job id that does
    not exist produces a foreign key error at insert, which is the
    database's job and a better place for it than a guess here."""
    assert parse_feedback_callback("fb:saved:999999999") == (FeedbackAction.SAVED, 999999999)


# --- the wording ---------------------------------------------------------


def test_no_feedback_produces_no_summary_line() -> None:
    assert current_feedback_summary([]) == ""


def test_the_summary_reports_every_action_not_just_the_latest() -> None:
    """The table keeps contradictions on purpose. Picking one here would
    hide that from the only person who could correct it."""
    summary = current_feedback_summary(
        [FeedbackAction.INTERESTED, FeedbackAction.NOT_RELEVANT]
    )

    assert "Interested" in summary
    assert "Not Relevant" in summary


def test_the_summary_uses_english_not_the_stored_identifier() -> None:
    summary = current_feedback_summary([FeedbackAction.NOT_RELEVANT])

    assert "Not Relevant" in summary
    assert "not_relevant" not in summary


# --- the service ---------------------------------------------------------


class _FakeUser:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class _FakeUserRepository:
    def __init__(self, user) -> None:
        self._user = user

    async def get_by_telegram_id(self, telegram_id):
        return self._user


class _FakeFeedbackRepository:
    """Enforces the real three-column rule in memory.

    Keyed on (user_id, job_id, action) exactly as
    uq_user_feedback_user_job_action is, so a test that passes here is
    testing the same rule the database enforces -- not a looser one that
    would let a two-column mistake through.
    """

    def __init__(self) -> None:
        self.rows: list[tuple[int, int, FeedbackAction]] = []

    async def record(self, *, user_id, job_id, action, recommendation_id=None):
        key = (user_id, job_id, action)
        if key in self.rows:
            return False
        self.rows.append(key)
        return True

    async def actions_for(self, user_id, job_id):
        return [
            action
            for (uid, jid, action) in self.rows
            if uid == user_id and jid == job_id
        ]


def _service(user_id: int = 2):
    service = FeedbackService.__new__(FeedbackService)
    service._session = None
    service._users = _FakeUserRepository(_FakeUser(user_id))
    service._feedback = _FakeFeedbackRepository()
    return service


def test_interested_is_recorded_and_acknowledged() -> None:
    service = _service()
    reply = _run(service.handle_callback(telegram_id=555, data="fb:interested:42"))

    assert service._feedback.rows == [(2, 42, FeedbackAction.INTERESTED)]
    assert "interested" in reply.text.lower()


def test_save_is_recorded_and_acknowledged() -> None:
    service = _service()
    reply = _run(service.handle_callback(telegram_id=555, data="fb:saved:42"))

    assert service._feedback.rows == [(2, 42, FeedbackAction.SAVED)]
    assert "Saved" in reply.text


def test_not_relevant_is_recorded_and_acknowledged() -> None:
    service = _service()
    reply = _run(service.handle_callback(telegram_id=555, data="fb:not_relevant:42"))

    assert service._feedback.rows == [(2, 42, FeedbackAction.NOT_RELEVANT)]
    assert "not relevant" in reply.text.lower()


def test_the_same_action_twice_writes_one_row(monkeypatch) -> None:
    """A double tap is not two opinions. Inline keyboards never expire,
    so this is an ordinary thing for a user to do."""
    service = _service()
    _run(service.handle_callback(telegram_id=555, data="fb:interested:42"))
    second = _run(service.handle_callback(telegram_id=555, data="fb:interested:42"))

    assert len(service._feedback.rows) == 1
    assert "already" in second.text.lower()


def test_a_repeat_is_acknowledged_rather_than_reported_as_an_error() -> None:
    """Nothing went wrong -- the row exists, which is what the person
    wanted. Wording it as a failure would teach users their taps are
    unreliable."""
    service = _service()
    _run(service.handle_callback(telegram_id=555, data="fb:saved:42"))
    second = _run(service.handle_callback(telegram_id=555, data="fb:saved:42"))

    assert "went wrong" not in second.text.lower()
    assert "error" not in second.text.lower()


def test_two_different_actions_on_one_job_write_two_rows() -> None:
    """Interested and Saved answer different questions."""
    service = _service()
    _run(service.handle_callback(telegram_id=555, data="fb:interested:42"))
    _run(service.handle_callback(telegram_id=555, data="fb:saved:42"))

    assert len(service._feedback.rows) == 2


def test_contradictory_feedback_is_kept_rather_than_overwritten() -> None:
    """The case a two-column unique constraint would silently destroy.

    "This looked appealing in a notification and stopped looking
    appealing once I read it" is the more interesting of the two facts,
    and it exists only while both rows do.
    """
    service = _service()
    _run(service.handle_callback(telegram_id=555, data="fb:interested:42"))
    reply = _run(service.handle_callback(telegram_id=555, data="fb:not_relevant:42"))

    assert len(service._feedback.rows) == 2
    assert (2, 42, FeedbackAction.INTERESTED) in service._feedback.rows
    assert (2, 42, FeedbackAction.NOT_RELEVANT) in service._feedback.rows
    assert "Interested" in reply.text and "Not Relevant" in reply.text


def test_the_same_action_on_two_different_jobs_writes_two_rows() -> None:
    service = _service()
    _run(service.handle_callback(telegram_id=555, data="fb:saved:42"))
    _run(service.handle_callback(telegram_id=555, data="fb:saved:43"))

    assert len(service._feedback.rows) == 2


def test_the_reply_reports_the_current_state_of_that_job() -> None:
    service = _service()
    reply = _run(service.handle_callback(telegram_id=555, data="fb:saved:42"))

    assert "Current feedback: Saved" in reply.text


def test_an_unknown_user_is_asked_to_start_rather_than_crashing() -> None:
    service = FeedbackService.__new__(FeedbackService)
    service._session = None
    service._users = _FakeUserRepository(None)
    service._feedback = _FakeFeedbackRepository()

    reply = _run(service.handle_callback(telegram_id=555, data="fb:saved:42"))

    assert "/start" in reply.text
    assert service._feedback.rows == []


def test_the_service_validates_its_own_input() -> None:
    """Reachable only from a script or a test -- the registered handler
    matches the prefix first. A service that trusts its caller to have
    validated is a service that is wrong the first time somebody
    forgets."""
    service = _service()
    reply = _run(service.handle_callback(telegram_id=555, data="onb:remote:yes"))

    assert service._feedback.rows == []
    assert "recognise" in reply.text.lower()
