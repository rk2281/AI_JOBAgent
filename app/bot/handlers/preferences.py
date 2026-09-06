"""Telegram adapters for /preferences.

Same four steps as every other handler in this package: pull
primitives out of the Update, open a session, call the service, send
the BotReply. No SQL, no branching on user state -- PreferencesService
already decided whether to refuse (onboarding incomplete), show the
menu, ask a follow-up question, or save a value; this file only knows
how to put a BotReply and a keyboard in front of a person.

preferences_callback mirrors onboarding.button_callback's shape
exactly -- answer the spinner, call the service, echo the tapped label
onto the question it answered when (and only when) something was
actually saved, otherwise just drop the keyboard. That echo logic
(tapped_button_label) is shared from app.bot.rendering rather than
copied a second time.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from app.bot.rendering import tapped_button_label, to_markup
from app.db.session import session_scope
from app.services.preferences import PreferencesService

logger = logging.getLogger(__name__)


async def preferences_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if update.message is None or update.effective_user is None:
        return

    async with session_scope() as session:
        reply = await PreferencesService(session).start_menu(update.effective_user.id)

    await update.message.reply_text(reply.text, reply_markup=to_markup(reply))


async def preferences_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle a "pref:<field>:<value>" button tap."""
    query = update.callback_query
    if query is None or update.effective_user is None:
        return

    # Stops Telegram's loading spinner immediately rather than after
    # the database round trip -- same reason onboarding.button_callback
    # does this first.
    await query.answer()

    async with session_scope() as session:
        outcome = await PreferencesService(session).handle_callback(
            telegram_id=update.effective_user.id, data=query.data or ""
        )

    if query.message is not None:
        try:
            if outcome.answered:
                # A value was actually saved: fold the tapped choice
                # into the question it answered and drop the keyboard,
                # so scrolling back shows one bubble carrying both.
                label = tapped_button_label(query)
                text = query.message.text or ""
                if label is not None:
                    text = f"{text}\n\n✅ {label}"
                await query.edit_message_text(text=text, reply_markup=None)
            else:
                # Either a menu tap that opened a sub-menu (nothing
                # saved yet -- the new question is sent as a fresh
                # message below) or a stale/unrecognised tap. Either
                # way, this keyboard is done: strip it so it cannot be
                # tapped twice.
                await query.edit_message_reply_markup(reply_markup=None)
        except Exception:  # noqa: BLE001 - message may be too old to edit
            logger.debug("Could not update answered preferences message", exc_info=True)

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=outcome.reply.text,
        reply_markup=to_markup(outcome.reply),
    )
