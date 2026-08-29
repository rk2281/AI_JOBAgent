"""Commands that do not touch onboarding state."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


HELP_TEXT = (
    "*What I do*\n"
    "I read your CV, learn what you're looking for, and message you when "
    "a matching job appears.\n\n"
    "*Commands*\n"
    "/start — begin or resume setup\n"
    "/status — see what I have on file\n"
    "/restart — redo setup from the beginning\n"
    "/help — this message\n"
    "/ping — check I'm alive\n\n"
    "You can send a new CV at any time to replace the one I have."
)


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if update.message is None:
        return
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


async def ping_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if update.message is None:
        return
    await update.message.reply_text("🏓 Pong! Bot is alive.")


async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Last line of defence for anything a handler let escape.

    Without this, an unhandled exception is logged by the library and
    the user simply never hears back — the worst failure mode in a chat
    interface, because it is indistinguishable from the bot being slow.
    """
    logger.exception("Unhandled error while processing update", exc_info=context.error)

    if isinstance(update, Update) and update.effective_message is not None:
        await update.effective_message.reply_text(
            "Something went wrong on my side. Please try again in a moment."
        )
