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
    "/profile — see what I understood from your CV\n"
    "/update_cv — replace your CV\n"
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


async def unknown_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Answer a button tap that matched no other callback handler.

    Exists because of what Day 11 changed above it. Until Day 11 the
    onboarding callback handler had no `pattern` and therefore matched
    EVERY callback query in the application -- which was the bug (a
    feedback tap reached the onboarding state machine), but it also
    meant every tap got answered by something.

    Giving both handlers a pattern fixes the routing and opens a hole
    underneath it: callback data matching neither `onb:` nor `fb:` now
    matches nothing, and an unanswered callback query leaves Telegram's
    loading spinner turning on the button forever. To the user that is
    indistinguishable from a bot that has died, which is exactly what
    error_handler below exists to prevent for messages.

    Registered LAST, so it can only ever see what the two prefix
    handlers declined. That ordering is load-bearing: registered first
    it would swallow everything, since it matches unconditionally.

    Prefer this over a negative-lookahead pattern on the onboarding
    handler (`^(?!fb:)`). That would work today and would need a new
    exclusion every time a prefix is added -- and a rule that has to be
    edited whenever anything else changes is a rule that will one day
    not be.
    """
    query = update.callback_query
    if query is None:
        return

    logger.warning("Callback query matched no handler")
    await query.answer(text="That button has expired.", show_alert=False)


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
