"""All database access concerning notification attempts and feedback.

Data access only. Nothing here decides whether a recommendation is
worth sending, what a message says, or what a repeated tap means -- the
service layer owns all three. What this file owns is the four SQL
shapes those decisions need, and two of them are shapes that only
PostgreSQL can express.

WHY THE DUPLICATE RULES LIVE IN SQL AND NOT IN PYTHON

Both are conflict rules, and a conflict rule enforced by reading first
and writing second is not enforced at all. Two processes -- the nightly
run and someone testing by hand, which is a combination Day 11
deliberately makes possible -- can interleave

    A: SELECT ... -> not sent yet
    B: SELECT ... -> not sent yet
    A: INSERT sent
    B: INSERT sent

and no care taken inside either one prevents it. So:

  * `uq_notification_sent_user_job`, a PARTIAL unique index over
    `status = 'SENT'` only, makes the second success impossible rather
    than unlikely. `mark_sent()` lets its IntegrityError out for the
    service to read as "somebody else already delivered this".

  * `uq_user_feedback_user_job_action` plus ON CONFLICT DO NOTHING
    makes a repeated tap a no-op inside one statement, with no window
    between the check and the write for a second tap to land in.

The service still checks before sending, because a query is cheaper
than a Telegram message and produces a better report. That check is an
optimisation and a courtesy. The index is the rule.

ON CONFLICT DO NOTHING, NEVER DO UPDATE

`record_feedback()` deliberately does not upsert. The row's created_at
means "when this person first said this", and DO UPDATE would push that
forward on every stray double tap, making the question unanswerable
from the data. Returning whether a row was actually inserted is enough
for the handler to say something honest either way.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.recommendation import (
    FeedbackAction,
    Notification,
    NotificationStatus,
    UserFeedback,
)


class NotificationRepository:
    """All database access concerning a Notification row."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def sent_job_ids(self, user_id: int) -> set[int]:
        """Which jobs this user has already been successfully sent.

        A set of ids in one query rather than a per-job existence check
        inside the delivery loop: the loop runs over a handful of
        recommendations and this runs once, so the alternative is N
        round trips to answer a question one round trip answers.

        Scoped to SENT only. `pending` and `failed` rows are attempts,
        not deliveries, and treating them as deliveries here would
        re-create exactly the lockout that the old unique constraint
        caused -- a single failed attempt permanently suppressing every
        later one.
        """
        result = await self._session.execute(
            select(Notification.job_id).where(
                Notification.user_id == user_id,
                Notification.status == NotificationStatus.SENT,
            )
        )
        return {row[0] for row in result.all()}

    async def open_attempt(
        self,
        *,
        user_id: int,
        job_id: int,
        recommendation_id: int | None,
        trigger_source: str,
    ) -> Notification:
        """Record that a delivery is about to be tried.

        Written BEFORE the Telegram call, not after, and the reason is
        the one AgentRunRepository.start() and ScoringRunRepository
        .start() both give: a process killed mid-call leaves evidence
        rather than silence. A row stuck at `pending` says "we were
        sending this when something stopped us", which is a fact worth
        having; no row at all is indistinguishable from never having
        tried.

        `pending` rows are outside the partial unique index, so any
        number of them may exist for one pair. That is what makes this
        write safe to do before knowing whether it will succeed.
        """
        attempt = Notification(
            user_id=user_id,
            job_id=job_id,
            recommendation_id=recommendation_id,
            status=NotificationStatus.PENDING,
            trigger_source=trigger_source,
        )
        self._session.add(attempt)
        await self._session.flush()
        return attempt

    async def mark_sent(self, attempt_id: int, sent_at: datetime) -> None:
        """Promote an attempt to SENT.

        THIS is where the partial unique index fires, and it is the
        only statement in the project that can raise IntegrityError as
        a normal outcome rather than as a bug. The caller catches it
        and counts a duplicate.

        Takes sent_at rather than calling now(), like every other
        repository here: a record that timestamps itself cannot be
        reproduced from its own inputs.
        """
        attempt = await self._session.get(Notification, attempt_id)
        if attempt is None:  # pragma: no cover - the caller just created it
            raise ValueError(f"no notifications row with id {attempt_id}")
        attempt.status = NotificationStatus.SENT
        attempt.sent_at = sent_at
        await self._session.flush()

    async def mark_failed(self, attempt_id: int, error_message: str) -> None:
        """Record why an attempt did not land.

        `error_message` must already be safe to store. The Telegram Bot
        API carries the bot token in its URL PATH, so a raw exception
        formatted into this column writes a live credential into the
        database permanently. Callers pass the output of
        describe_telegram_error(); this method does no formatting of
        its own precisely so there is nowhere here for an exception to
        be stringified by accident.
        """
        attempt = await self._session.get(Notification, attempt_id)
        if attempt is None:  # pragma: no cover - the caller just created it
            raise ValueError(f"no notifications row with id {attempt_id}")
        attempt.status = NotificationStatus.FAILED
        attempt.error_message = error_message
        await self._session.flush()


class FeedbackRepository:
    """All database access concerning a UserFeedback row."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        user_id: int,
        job_id: int,
        action: FeedbackAction,
        recommendation_id: int | None = None,
    ) -> bool:
        """Insert one reaction. Returns True if it was new.

        ON CONFLICT DO NOTHING against
        `uq_user_feedback_user_job_action`, so:

          * the same action twice writes one row and reports False the
            second time,
          * a DIFFERENT action for the same job writes a second row and
            reports True, because that is a different opinion and not a
            repeat.

        The returned boolean is what lets the handler tell a person
        "saved" from "you already saved this" without a second query,
        and without either message being a guess. RETURNING id is empty
        exactly when the conflict clause suppressed the insert.
        """
        statement = (
            pg_insert(UserFeedback)
            .values(
                user_id=user_id,
                job_id=job_id,
                action=action,
                recommendation_id=recommendation_id,
            )
            .on_conflict_do_nothing(constraint="uq_user_feedback_user_job_action")
            .returning(UserFeedback.id)
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none() is not None

    async def actions_for(self, user_id: int, job_id: int) -> list[FeedbackAction]:
        """Every reaction this user has recorded against this job.

        A list and not a single value, because the table deliberately
        keeps contradictions: "Interested" followed by "Not Relevant"
        is two rows and collapsing them here would throw away the thing
        the three-column constraint exists to preserve. Ordered by
        created_at so the caller can show the latest without deciding
        that the earlier ones did not happen.
        """
        result = await self._session.execute(
            self._actions_query(user_id, job_id)
        )
        return list(result.scalars().all())

    @staticmethod
    def _actions_query(user_id: int, job_id: int) -> Select:
        return (
            select(UserFeedback.action)
            .where(UserFeedback.user_id == user_id, UserFeedback.job_id == job_id)
            .order_by(UserFeedback.created_at.asc(), UserFeedback.id.asc())
        )
