"""Turning a framework-free BotReply into something Telegram can send.

This is the only place that knows both sides. Services produce
BotReply; this module converts it to Telegram's types.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.services.replies import BotReply


def to_markup(reply: BotReply) -> InlineKeyboardMarkup | None:
    """Build an inline keyboard, or None when the reply has no buttons."""
    if not reply.has_keyboard:
        return None

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(button.label, callback_data=button.callback_data)
                for button in row
            ]
            for row in reply.buttons
        ]
    )


def tapped_button_label(query) -> str | None:
    """The label of the button whose callback_data matches this tap.

    Read back from the keyboard that was actually sent, rather than
    re-deriving a label from the raw callback value at the call site --
    that keyboard is the one thing the user actually saw, so the echo
    can never drift from it the way a second hand-written copy could.

    Shared by every callback handler that echoes a tapped choice onto
    the question it answered (onboarding, preferences) -- moved here
    rather than kept private to one handler once a second one needed
    the identical logic.
    """
    markup = query.message.reply_markup if query.message is not None else None
    if markup is None:
        return None

    for row in markup.inline_keyboard:
        for button in row:
            if button.callback_data == query.data:
                return button.text
    return None
