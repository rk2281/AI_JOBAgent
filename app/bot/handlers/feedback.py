"""The handler behind the three buttons on every job notification.

Same four-step shape as every other handler here: answer the callback,
open a session, ask a service what to say, render the answer. No SQL,
no decisions -- FeedbackService owns both.

WHY THE KEYBOARD IS LEFT ON THE MESSAGE

onboarding.button_callback strips the keyboard off after a tap, so an
old question cannot be answered twice. This deliberately does not.

The two cases are opposites. An onboarding button carries an ANSWER to
a question the flow has since moved past, and applying it later would
corrupt a state machine -- so the button must stop being tappable. A
feedback button carries an OPINION about one specific job, and an
opinion about job 42 means the same thing whenever it is expressed. A
user who taps Interested and later decides otherwise must still be able
to reach Not Relevant, and stripping the keyboard would take that away
permanently for the sake of preventing a double tap that the database
already handles.

So the buttons stay, repeat taps are absorbed by the unique constraint,
and the reply says which state the job is now in.

WHY THE REPLY IS AN ANSWER AND NOT A NEW MESSAGE

query.answer(text=...) shows a small toast over the chat rather than
pushing another message into it. A user notified about three jobs who
taps all three buttons would otherwise turn a three-message
notification into a nine-message conversation.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from app.db.session import session_scope
from app.services.feedback import FeedbackService

logger = logging.getLogger(__name__)

# Telegram truncates a callback answer beyond roughly 200 characters.
# The reply is two short lines, so this only ever bites if a future
# summary grows -- in which case truncation is better than the API
# rejecting the answer and leaving the button spinning.
_MAX_ANSWER_CHARS = 200


async def feedback_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle a tap on Interested / Save / Not Relevant.

    Registered with pattern=^fb: so it receives feedback callbacks and
    nothing else. Before Day 11 the onboarding handler carried NO
    pattern, which meant it matched every callback query in the
    application -- these included.
    """
    query = update.callback_query
    if query is None or update.effective_user is None:
        return

    try:
        async with session_scope() as session:
            reply = await FeedbackService(session).handle_callback(
                telegram_id=update.effective_user.id,
                data=query.data or "",
            )
        text = reply.text
    except Exception:  # noqa: BLE001 - the tap must always be answered
        # A spinning button is indistinguishable from a bot that has
        # crashed, so the answer below happens on every path. The
        # exception is logged in full here, where it is going to a log
        # and not to a Telegram request; nothing in this handler touches
        # the Bot API URL that carries the token.
        logger.exception("Feedback callback failed")
        text = "Something went wrong recording that. Please try again."

    # Answered AFTER the database work, unlike onboarding, which answers
    # first to stop the spinner early. Here the answer IS the reply --
    # answering first would mean answering twice, and Telegram accepts
    # only the first answer per query.
    await query.answer(text=text[:_MAX_ANSWER_CHARS], show_alert=False)
