"""Recording what a user thought of a recommended job.

Same shape as OnboardingService: it is handed a telegram_id and the raw
callback string, it returns a BotReply, and it imports nothing from
python-telegram-bot. The handler decides how Telegram renders the
answer; this decides what the answer is.

WHY A SECOND TAP IS ACKNOWLEDGED RATHER THAN CORRECTED

Inline keyboards never expire. A user can scroll up to a notification
from three weeks ago and tap Interested a second time, and the honest
response is not an error -- nothing went wrong, and their opinion is
already recorded. The database drops the repeat via ON CONFLICT DO
NOTHING and the reply says so plainly.

That is deliberately different from onboarding's treatment of an old
button, which re-sends the current question. There the button carried
an ANSWER to a question that has since moved on, so applying it would
corrupt a state machine. Here the button carries an OPINION about a
specific job, and an opinion about job 42 means the same thing whenever
it is expressed.

WHY CONTRADICTORY FEEDBACK IS KEPT

"Interested" then "Not Relevant" writes two rows and the reply reports
both. The alternative -- overwrite, so the latest wins -- would throw
away the more interesting of the two facts: that this job looked
appealing in a notification and stopped looking appealing once it was
read. That gap is exactly the signal a later re-ranking wants, and it
exists only while both rows do.

The cost is that "what does this user currently think of job 42" has no
single answer, which is why current_feedback_summary() reports the
whole set rather than picking one and calling it the truth.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.recommendation import FeedbackAction
from app.db.repositories.notification import FeedbackRepository
from app.db.repositories.user import UserRepository
from app.services.notification_message import CALLBACK_PREFIX
from app.services.replies import BotReply

logger = logging.getLogger(__name__)

# What each action is called when spoken back to a person. The enum
# values are storage identifiers ("not_relevant"); these are English.
_ACTION_LABELS = {
    FeedbackAction.INTERESTED: "Interested",
    FeedbackAction.SAVED: "Saved",
    FeedbackAction.NOT_RELEVANT: "Not Relevant",
}

_ACKNOWLEDGEMENTS = {
    FeedbackAction.INTERESTED: "\U0001f44d Marked as interested.",
    FeedbackAction.SAVED: "\U0001f516 Saved.",
    FeedbackAction.NOT_RELEVANT: "\U0001f6ab Noted — not relevant.",
}


def parse_feedback_callback(data: str) -> tuple[FeedbackAction, int] | None:
    """"fb:saved:1234" -> (SAVED, 1234). None if it is not ours.

    Returns None rather than raising, and the caller treats None as
    "not a feedback button" rather than as an error. Three parts and
    the "fb" prefix, matching OnboardingService.handle_callback()'s
    parser exactly -- one convention for callback data across the
    project, not two.

    Validates the action against the enum rather than trusting the
    string. callback_data is round-tripped through Telegram's servers
    and comes back as whatever was in the message, so it is input from
    outside the process even though this process wrote it.
    """
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != CALLBACK_PREFIX:
        return None

    _, raw_action, raw_job_id = parts

    try:
        action = FeedbackAction(raw_action)
    except ValueError:
        logger.warning("Unrecognised feedback action in callback data")
        return None

    try:
        job_id = int(raw_job_id)
    except ValueError:
        logger.warning("Unrecognised job id in feedback callback data")
        return None

    return action, job_id


def current_feedback_summary(actions: list[FeedbackAction]) -> str:
    """"Current feedback: Interested, Not Relevant", or "" for none.

    A pure function over the list so the wording can be tested without
    a database. Reports every action rather than the latest, because
    the table keeps contradictions on purpose and picking one here
    would hide that from the only person who could correct it.

    Order is the order the rows were recorded in, and duplicates cannot
    occur -- the unique constraint sees to that -- so no de-duplication
    is needed and none is done. Doing it anyway would mask a broken
    constraint.
    """
    if not actions:
        return ""
    labels = ", ".join(_ACTION_LABELS[action] for action in actions)
    return f"Current feedback: {labels}"


class FeedbackService:
    """Turns a feedback button tap into a row and a sentence."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._feedback = FeedbackRepository(session)
        self._users = UserRepository(session)

    async def handle_callback(self, telegram_id: int, data: str) -> BotReply:
        """Record one reaction and describe the result.

        Resolves telegram_id to the internal user id rather than
        trusting the callback to carry it. The callback data holds only
        an action and a job id, deliberately: a user id in a button
        would be a user id a person could edit, and Telegram delivers
        the tapper's own account alongside the data for free.
        """
        parsed = parse_feedback_callback(data)
        if parsed is None:
            # Unreachable through the registered handler, which matches
            # on the prefix before this is called. Kept because the
            # service is callable from a script and from tests, and a
            # service that trusts its caller to have validated is a
            # service that is wrong the first time someone forgets.
            return BotReply(text="That button is not one I recognise.")

        action, job_id = parsed

        user = await self._users.get_by_telegram_id(telegram_id)
        if user is None:
            # A tap from somebody with no row at all. Possible if the
            # database was reset under a live message.
            return BotReply(text="I don't have a profile for you yet. Send /start.")

        created = await self._feedback.record(
            user_id=user.id,
            job_id=job_id,
            action=action,
        )

        actions = await self._feedback.actions_for(user.id, job_id)
        summary = current_feedback_summary(actions)

        if created:
            text = _ACKNOWLEDGEMENTS[action]
        else:
            # Not an error, and worded so it does not read as one. The
            # row exists, which is what the person wanted.
            text = f"Already recorded as {_ACTION_LABELS[action]}."

        return BotReply(text=f"{text}\n\n{summary}" if summary else text)
