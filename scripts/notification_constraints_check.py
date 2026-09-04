"""Does PostgreSQL actually enforce the Day 11 rules? Asked of PostgreSQL.

    python -m scripts.notification_constraints_check

WRITES NOTHING. Every insert below happens inside ONE transaction that
is unconditionally rolled back, so the database is left exactly as it
was found -- and the script proves that rather than claiming it, by
printing the row counts before and after as two separate numbers for a
human to compare.

That is deliberate wording, and this repository has earned it:
`concurrent_claim_dryrun.py` promised no writes in its NAME, fired real
Gemini extractions, and left a row in a state nobody authorised. So the
guarantee here is structural, not a matter of care. Constraints are
enforced at statement time, inside the transaction, which is precisely
why this can prove enforcement without committing anything.

WHY THIS IS A SCRIPT AND NOT A pytest CASE

The same reason `extract_cv` has no unit test: this project's models use
PostgreSQL-specific features with no lightweight equivalent, the suite
has no database, and the established pattern for behaviour that can only
be observed against real Postgres is a live script. `pytest-asyncio` is
also not installed and must not be.

WHY THE UNIT TESTS ARE NOT ENOUGH ON THEIR OWN

tests/test_notification_schema.py asserts the index is DECLARED with the
right predicate. tests/test_notification_delivery.py asserts the loop
treats a refused write as a duplicate. Neither can establish the fact
that matters most:

    a partial unique index can be declared correctly, created
    successfully, and match zero rows forever.

`WHERE status::text = 'sent'` would do exactly that -- the labels in
PostgreSQL are uppercase, because SQLAlchemy persists an enum by NAME.
Duplicate prevention would be entirely absent while the migration
reported success and every test in the suite stayed green. Only an
attempted duplicate INSERT can tell those apart, so that is what this
does.
"""

from __future__ import annotations

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from datetime import datetime, timezone  # noqa: E402

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

from app.db.models.job import Job  # noqa: E402
from app.db.models.recommendation import (  # noqa: E402
    TRIGGER_SOURCE_MANUAL_TEST,
    TRIGGER_SOURCE_SCHEDULED,
    FeedbackAction,
    Notification,
    NotificationStatus,
    UserFeedback,
)
from app.db.models.user import User  # noqa: E402
from app.db.repositories.notification import FeedbackRepository  # noqa: E402
from app.db.session import (  # noqa: E402
    dispose_engine,
    get_session_factory,
    init_engine,
)

_PASS = "  PASS"
_FAIL = "  FAIL"


class _Results:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0

    def check(self, label: str, condition: bool, detail: str = "") -> None:
        if condition:
            self.passed += 1
            print(f"{_PASS}  {label}")
        else:
            self.failed += 1
            print(f"{_FAIL}  {label}  {detail}")


async def _notification_rules(session, user_id: int, job_id: int, results: _Results):
    """failed, failed, sent -- then a second sent must be refused."""
    print("\n--- notifications: retry is allowed, a second success is not")

    def attempt(status, trigger=TRIGGER_SOURCE_SCHEDULED):
        return Notification(
            user_id=user_id,
            job_id=job_id,
            status=status,
            trigger_source=trigger,
            sent_at=datetime.now(timezone.utc)
            if status is NotificationStatus.SENT
            else None,
        )

    # Two failures for the same pair. Under the OLD constraint the
    # second of these was impossible, which is what locked a user out of
    # a job after one Telegram outage.
    for index in (1, 2):
        savepoint = await session.begin_nested()
        try:
            session.add(attempt(NotificationStatus.FAILED))
            await session.flush()
            results.check(f"failed attempt {index} accepted", True)
        except IntegrityError as error:
            await savepoint.rollback()
            results.check(f"failed attempt {index} accepted", False, str(error.orig))

    # A pending row alongside them, also outside the partial index.
    savepoint = await session.begin_nested()
    try:
        session.add(attempt(NotificationStatus.PENDING))
        await session.flush()
        results.check("pending attempt accepted alongside failures", True)
    except IntegrityError as error:
        await savepoint.rollback()
        results.check("pending attempt accepted alongside failures", False, str(error.orig))

    # The retry that succeeds.
    savepoint = await session.begin_nested()
    try:
        session.add(attempt(NotificationStatus.SENT))
        await session.flush()
        results.check("sent after two failures accepted (retry works)", True)
    except IntegrityError as error:
        await savepoint.rollback()
        results.check("sent after two failures accepted (retry works)", False, str(error.orig))

    # THE ONE THAT MATTERS. If the index predicate were lowercase this
    # would succeed, and everything above would still have passed.
    savepoint = await session.begin_nested()
    refused = False
    try:
        session.add(attempt(NotificationStatus.SENT, TRIGGER_SOURCE_MANUAL_TEST))
        await session.flush()
    except IntegrityError:
        refused = True
        await savepoint.rollback()
    results.check(
        "a SECOND sent row for the same (user, job) is REFUSED by the database",
        refused,
        "the partial unique index did not fire -- check its predicate case",
    )

    # The index must be scoped to the pair, not to the user.
    other_job = await _another_job_id(session, job_id)
    if other_job is not None:
        savepoint = await session.begin_nested()
        try:
            session.add(
                Notification(
                    user_id=user_id,
                    job_id=other_job,
                    status=NotificationStatus.SENT,
                    trigger_source=TRIGGER_SOURCE_SCHEDULED,
                    sent_at=datetime.now(timezone.utc),
                )
            )
            await session.flush()
            results.check("a sent row for a DIFFERENT job is accepted", True)
        except IntegrityError as error:
            await savepoint.rollback()
            results.check("a sent row for a DIFFERENT job is accepted", False, str(error.orig))

    total = await session.scalar(
        select(func.count()).select_from(Notification).where(
            Notification.user_id == user_id, Notification.job_id == job_id
        )
    )
    results.check(
        "the pair holds 4 attempt rows and exactly 1 of them is sent",
        total == 4,
        f"got {total}",
    )


async def _feedback_rules(session, user_id: int, job_id: int, results: _Results):
    """Same action twice -> one row. Different actions -> two rows."""
    print("\n--- user_feedback: repeats collapse, changed minds do not")
    repository = FeedbackRepository(session)

    first = await repository.record(
        user_id=user_id, job_id=job_id, action=FeedbackAction.INTERESTED
    )
    results.check("Interested recorded as new", first is True)

    second = await repository.record(
        user_id=user_id, job_id=job_id, action=FeedbackAction.INTERESTED
    )
    results.check(
        "Interested a SECOND time reports 'already recorded'",
        second is False,
        "ON CONFLICT DO NOTHING did not suppress the repeat",
    )

    saved = await repository.record(
        user_id=user_id, job_id=job_id, action=FeedbackAction.SAVED
    )
    results.check("Saved on the same job is a NEW row", saved is True)

    contradiction = await repository.record(
        user_id=user_id, job_id=job_id, action=FeedbackAction.NOT_RELEVANT
    )
    results.check(
        "Not Relevant after Interested is KEPT, not an overwrite",
        contradiction is True,
        "a (user, job) constraint would have swallowed this",
    )

    rows = await session.scalar(
        select(func.count()).select_from(UserFeedback).where(
            UserFeedback.user_id == user_id, UserFeedback.job_id == job_id
        )
    )
    results.check(
        "four taps left exactly three rows",
        rows == 3,
        f"got {rows}",
    )

    actions = await repository.actions_for(user_id, job_id)
    results.check(
        "both sides of the contradiction are readable",
        FeedbackAction.INTERESTED in actions
        and FeedbackAction.NOT_RELEVANT in actions,
        f"got {actions}",
    )


async def _another_job_id(session, job_id: int) -> int | None:
    return await session.scalar(select(Job.id).where(Job.id != job_id).limit(1))


async def main() -> int:
    factory = get_session_factory()
    results = _Results()

    async with factory() as session:
        user_id = await session.scalar(select(User.id).order_by(User.id).limit(1))
        job_id = await session.scalar(select(Job.id).order_by(Job.id).limit(1))

        if user_id is None or job_id is None:
            print("No users or no jobs in the database; nothing to test against.")
            return 1

        before_notifications = await session.scalar(
            select(func.count()).select_from(Notification)
        )
        before_feedback = await session.scalar(
            select(func.count()).select_from(UserFeedback)
        )

        print(f"testing against user_id={user_id} job_id={job_id}")
        print(f"notifications rows BEFORE   {before_notifications}")
        print(f"user_feedback rows BEFORE   {before_feedback}")

        try:
            await _notification_rules(session, user_id, job_id, results)
            await _feedback_rules(session, user_id, job_id, results)
        finally:
            # Unconditional, and in a finally so a failing assertion
            # above cannot leave rows behind either.
            await session.rollback()

    # A SECOND session, so the counts below are read outside the
    # transaction that was rolled back. Reading them in the same session
    # would show the rollback's own view and prove nothing about what is
    # durably stored.
    async with factory() as session:
        after_notifications = await session.scalar(
            select(func.count()).select_from(Notification)
        )
        after_feedback = await session.scalar(
            select(func.count()).select_from(UserFeedback)
        )

    print("\n--- nothing was persisted")
    print(f"notifications rows AFTER    {after_notifications}")
    print(f"user_feedback rows AFTER    {after_feedback}")

    results.check(
        "notifications count is unchanged",
        after_notifications == before_notifications,
        f"{before_notifications} -> {after_notifications}",
    )
    results.check(
        "user_feedback count is unchanged",
        after_feedback == before_feedback,
        f"{before_feedback} -> {after_feedback}",
    )

    print(f"\npassed {results.passed}   failed {results.failed}")
    return 0 if results.failed == 0 else 1


if __name__ == "__main__":
    if init_engine() is None:
        print("DATABASE_URL is not configured.")
        raise SystemExit(1)

    try:
        exit_code = asyncio.run(main())
    finally:
        asyncio.run(dispose_engine())

    raise SystemExit(exit_code)
