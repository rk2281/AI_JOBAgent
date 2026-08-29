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
