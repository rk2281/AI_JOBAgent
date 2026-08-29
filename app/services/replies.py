"""What a service hands back to a handler.

These are plain dataclasses with no python-telegram-bot imports. The
service decides *what to say*; the handler decides *how Telegram
renders it*. That is what makes the onboarding flow testable without a
bot token, a network, or a running Telegram server.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Button:
    label: str
    callback_data: str


@dataclass(frozen=True)
class BotReply:
    """One message the bot should send back."""

    text: str

    # Each inner list is one row of buttons. Empty means no keyboard.
    buttons: list[list[Button]] = field(default_factory=list)

    @property
    def has_keyboard(self) -> bool:
        return bool(self.buttons)
