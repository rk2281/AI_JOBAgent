"""The command menu, /help, and the actually-registered commands agree.

app.bot.commands.COMMANDS is the one list read by register_handlers()
(app/bot/handlers/__init__.py), /help's text (app/bot/handlers/common.py)
and app/main.py's set_my_commands call. register_handlers() already
loops over COMMANDS to decide what to register, so the registered set
can only ever be a subset of it (a name present in COMMANDS but missing
from _COMMAND_HANDLERS raises KeyError at registration time, loudly).
What that loop cannot catch on its own is a command handler defined in
_COMMAND_HANDLERS and never added to COMMANDS -- silently un-menued,
missing from /help, present in the app -- so this file asserts equality
in both directions against a real Application, following the same
pattern tests/test_callback_routing.py uses for callback routing.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from telegram.ext import CommandHandler

from app.bot.commands import COMMANDS
from app.bot.handlers import common, register_handlers


class _FakeApplication:
    """Records what register_handlers() adds, in order."""

    def __init__(self) -> None:
        self.handlers: list = []
        self.error_handlers: list = []

    def add_handler(self, handler) -> None:
        self.handlers.append(handler)

    def add_error_handler(self, handler) -> None:
        self.error_handlers.append(handler)


def _registered() -> _FakeApplication:
    application = _FakeApplication()
    register_handlers(application)
    return application


def _registered_command_names() -> set[str]:
    names: set[str] = set()
    for handler in _registered().handlers:
        if isinstance(handler, CommandHandler):
            names |= handler.commands
    return names


def test_every_command_in_the_list_is_actually_registered() -> None:
    registered = _registered_command_names()
    listed = {command.name for command in COMMANDS}

    assert listed <= registered, (
        "a name in app.bot.commands.COMMANDS was never registered as a "
        "CommandHandler -- it would appear in /help and the \"/\" menu "
        "but do nothing when tapped"
    )


def test_no_registered_command_is_missing_from_the_list() -> None:
    registered = _registered_command_names()
    listed = {command.name for command in COMMANDS}

    assert registered <= listed, (
        "a CommandHandler is registered for a name absent from "
        "app.bot.commands.COMMANDS -- it would work, but be invisible "
        "to /help and Telegram's own command menu"
    )


def test_command_names_have_no_duplicates() -> None:
    names = [command.name for command in COMMANDS]
    assert len(names) == len(set(names))


def test_help_text_mentions_every_command() -> None:
    for command in COMMANDS:
        assert f"/{command.name}" in common.HELP_TEXT


def test_help_text_carries_a_description_for_each_command() -> None:
    for command in COMMANDS:
        assert command.description in common.HELP_TEXT


def test_help_text_carries_no_leftover_bold_markup() -> None:
    """parse_mode was removed from the SEND call (see
    test_help_command_sends_with_no_parse_mode), but the "*...*" bold
    markers were never removed from HELP_TEXT itself -- so a live
    /help rendered literal asterisks around "What I do" and "Commands"
    instead of bold text. That test proves the send call is safe; this
    one proves the text is actually clean, which is a different claim.

    Narrower than banning every Markdown special character: `/update_cv`
    permanently contains a real underscore that is not sanitisable
    input (see the long comment above), so a blanket character ban
    would fail against the one piece of content that caused the
    original incident. `*` carries no such legitimate use here --
    nothing in COMMANDS ever needs one -- so it is safe to ban outright
    rather than merely tolerate.
    """
    assert "*" not in common.HELP_TEXT


# tests/test_onboarding.py::test_prompts_contain_no_markdown_special_characters
# guards OnboardingService's prompts by asserting NONE of those Markdown
# characters appear anywhere in the text. That exact rule cannot be
# copied onto HELP_TEXT: "/update_cv" is a real, permanent command name
# containing an underscore, not sanitisable input, so a test banning the
# character from this string would fail against the one piece of content
# that caused the incident below -- asserting the wrong thing "passes"
# by being wrong about what's safe, not by proving anything.
#
# What actually broke production on 2026-09-05 was help_command sending
# HELP_TEXT with parse_mode="Markdown": a live /help crashed with
# `telegram.error.BadRequest: Can't parse entities` because that
# underscore opened an italic entity Telegram's legacy parser never
# found a close for. The rule that mattered was never "this text has no
# special characters" -- HELP_TEXT always did -- it was "a string that
# might contain them must never be sent with parse_mode". parse_mode is
# now gone from help_command; this guards the CALL SITE against it
# quietly coming back, which is the thing that would actually crash
# again, rather than re-asserting a property of the text that was never
# true and is not the point.
def test_help_command_sends_with_no_parse_mode() -> None:
    async def run() -> None:
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(message=message)

        await common.help_command(update, context=None)

        message.reply_text.assert_awaited_once()
        _, kwargs = message.reply_text.await_args
        assert kwargs.get("parse_mode") is None, (
            "help_command passed parse_mode again -- HELP_TEXT contains "
            "command names with underscores (e.g. /update_cv) that "
            "Telegram's legacy Markdown parser cannot render safely"
        )

    asyncio.run(run())
