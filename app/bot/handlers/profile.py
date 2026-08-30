"""Telegram adapters for /profile and /update_cv.

Same four steps as every other handler in this package: pull
primitives out of the Update, open a session, call the service, send
the BotReply. No SQL, no branching on user state.

Neither handler schedules background work. /profile is read-only by
design, and /update_cv cannot do anything until the user actually
sends a file — at which point onboarding.document_message, which
already handles a replacement CV from a COMPLETE user, takes over and
schedules extraction exactly as it does for a first upload.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from app.bot.rendering import to_markup
from app.db.session import session_scope
from app.services.profile import ProfileService

logger = logging.getLogger(__name__)


async def profile_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if update.message is None or update.effective_user is None:
        return

    async with session_scope() as session:
        reply = await ProfileService(session).show(update.effective_user.id)

    # No parse_mode. reply.text carries user-derived text — filenames,
    # CV fields, provider error messages — and Telegram's legacy
    # Markdown parser has no escape mechanism: a stray "_" or "." in
    # any of that text opens an entity that never closes and the whole
    # message is rejected with a 400. Plain text has no such failure
    # mode, at the cost of the bold labels.
    await update.message.reply_text(
        reply.text,
        reply_markup=to_markup(reply),
    )


async def update_cv_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if update.message is None or update.effective_user is None:
        return

    async with session_scope() as session:
        reply = await ProfileService(session).request_update(
            update.effective_user.id
        )

    await update.message.reply_text(reply.text, reply_markup=to_markup(reply))
