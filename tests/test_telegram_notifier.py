"""The Telegram integration: error classification, and the token in the URL.

Half of this file is a security test, and it is the half that matters.

Every Bot API call is https://api.telegram.org/bot<TOKEN>/sendMessage,
so the URL IS a credential -- the same property that made Adzuna's
app_id and app_key leak into logs for months, with the credential moved
one URL component over. The tests below assert that nothing this module
produces can carry it: not the string handed to a logger, and
emphatically not the string written to notifications.error_message,
which is a database column a human reads back weeks later.

No bot token, no network. TelegramNotifier.send() is driven with a fake
bot; describe_telegram_error() is a pure function.
"""

from __future__ import annotations

import asyncio

import pytest
from telegram.error import (
    BadRequest,
    Forbidden,
    InvalidToken,
    NetworkError,
    RetryAfter,
    TelegramError,
    TimedOut,
)

from app.integrations import telegram as telegram_integration
from app.integrations.telegram import (
    SendResult,
    TelegramNotifier,
    describe_telegram_error,
)
from app.services.replies import BotReply, Button


def _run(coro):
    return asyncio.run(coro)


# A token-shaped string. Not a real credential -- it is invented here so
# the test can prove it does NOT come out the other end.
_FAKE_TOKEN = "1234567890:AAFfakefakefakefakefakefakefakefake"


class _FakeBot:
    """Stands in for telegram.Bot. Records calls, raises what it is told."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict] = []
        self.initialised = False
        self.shut_down = False

    async def initialize(self) -> None:
        self.initialised = True

    async def shutdown(self) -> None:
        self.shut_down = True

    async def send_message(self, **kwargs) -> None:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error


def _notifier(bot: _FakeBot) -> TelegramNotifier:
    notifier = TelegramNotifier.__new__(TelegramNotifier)
    notifier._bot = bot
    return notifier


def _reply() -> BotReply:
    return BotReply(text="hello", buttons=[[Button("Interested", "fb:interested:1")]])


# --- the token must never come out ---------------------------------------


def test_a_network_error_carrying_the_url_is_not_described_verbatim() -> None:
    """The Adzuna incident, in its Telegram form.

    httpx formats a request URL into its own exception string. For
    Telegram that URL contains the bot token, so `str(exc)` -- the
    ordinary, careful-looking thing to log -- writes a live credential.
    """
    leaked = (
        "Connection error for url "
        f"'https://api.telegram.org/bot{_FAKE_TOKEN}/sendMessage'"
    )
    description = describe_telegram_error(NetworkError(leaked))

    assert _FAKE_TOKEN not in description
    assert "api.telegram.org" not in description


def test_the_configured_token_is_redacted_from_any_description(monkeypatch) -> None:
    """The belt under the braces.

    describe_telegram_error() already reports only a class name and the
    API's own description, neither of which contains the token. That is
    a claim about a third-party library's formatting, true until some
    version it is not -- and the cost of being wrong once is a permanent
    credential in a database column.
    """
    monkeypatch.setattr(
        telegram_integration.settings, "telegram_bot_token", _FAKE_TOKEN
    )

    description = describe_telegram_error(
        BadRequest(f"something went wrong with {_FAKE_TOKEN} here")
    )

    assert _FAKE_TOKEN not in description
    assert "***" in description


def test_an_invalid_token_error_never_quotes_the_token(monkeypatch) -> None:
    """InvalidToken's own message can name the token it rejected, so this
    branch discards the message entirely rather than redacting it."""
    monkeypatch.setattr(
        telegram_integration.settings, "telegram_bot_token", _FAKE_TOKEN
    )

    description = describe_telegram_error(InvalidToken(f"token {_FAKE_TOKEN} invalid"))

    assert _FAKE_TOKEN not in description
    assert description == "invalid bot token"


def test_a_failed_send_produces_no_token_in_its_error(monkeypatch) -> None:
    """End to end through send(), which is the string that reaches the
    database."""
    monkeypatch.setattr(
        telegram_integration.settings, "telegram_bot_token", _FAKE_TOKEN
    )
    bot = _FakeBot(
        error=NetworkError(f"https://api.telegram.org/bot{_FAKE_TOKEN}/sendMessage")
    )

    result = _run(_notifier(bot).send(chat_id=1, reply=_reply()))

    assert result.ok is False
    assert _FAKE_TOKEN not in (result.error or "")


def test_bad_request_is_a_network_error_subclass() -> None:
    """Pinning the hierarchy, because it is not what it looks like and it
    has already caused two bugs in describe_telegram_error().

    BadRequest is an API-level error that inherits from the
    transport-level NetworkError. So `isinstance(err, NetworkError)`
    catches it, and an implementation that handles NetworkError first --
    which is the obvious way to keep httpx text out of the output --
    silently throws away every API description as a side effect.

    If python-telegram-bot ever normalises this hierarchy, this test
    fails and the ordering in describe_telegram_error() should be
    re-read rather than the test deleted.
    """
    assert issubclass(BadRequest, NetworkError)
    assert not issubclass(Forbidden, NetworkError)


def test_an_api_error_still_reports_why_the_request_was_rejected() -> None:
    """The regression guarded against above.

    A delivery failure recorded as "connection failed (BadRequest)"
    would be actively misleading in notifications.error_message: it says
    the network was at fault when Telegram gave a precise reason.
    """
    description = describe_telegram_error(BadRequest("Bad Request: chat not found"))

    # Compared case-insensitively: PTB normalises the API's string,
    # stripping the "Bad Request: " prefix and capitalising, so
    # `.message` is "Chat not found". _is_unreachable() lowercases
    # before matching for the same reason.
    assert "chat not found" in description.lower()
    assert "connection failed" not in description


def test_a_transport_error_reports_no_message_at_all() -> None:
    """The other side of the same line. TimedOut is a genuine
    NetworkError and its message is httpx's, so only the class is
    reported -- exactly as describe_http_error() treats an httpx
    TransportError."""
    assert describe_telegram_error(TimedOut()) == "connection failed (TimedOut)"


def test_describe_never_returns_the_raw_exception_string() -> None:
    """A general form of the rule, so a new exception type added later
    cannot fall through to str(exc)."""
    for error in (
        NetworkError("raw url text"),
        TimedOut(),
        RetryAfter(30),
        TelegramError("plain"),
        ValueError("not even a telegram error"),
    ):
        description = describe_telegram_error(error)
        assert isinstance(description, str) and description


# --- classification ------------------------------------------------------


def test_a_blocked_user_is_reported_as_permanently_unreachable() -> None:
    bot = _FakeBot(error=Forbidden("Forbidden: bot was blocked by the user"))
    result = _run(_notifier(bot).send(chat_id=1, reply=_reply()))

    assert result.ok is False
    assert result.user_unreachable is True
    assert result.configuration_error is False


def test_a_missing_chat_is_reported_as_permanently_unreachable() -> None:
    bot = _FakeBot(error=BadRequest("Bad Request: chat not found"))
    result = _run(_notifier(bot).send(chat_id=1, reply=_reply()))

    assert result.user_unreachable is True


def test_an_ordinary_bad_request_is_not_treated_as_a_blocked_user() -> None:
    """BadRequest covers a malformed message as happily as a missing
    chat. Erring toward transient is the safe direction: a transient
    error misread as permanent DEACTIVATES a real user and silently
    stops every future notification for them, where the opposite
    mistake costs one wasted API call."""
    bot = _FakeBot(error=BadRequest("Bad Request: message text is empty"))
    result = _run(_notifier(bot).send(chat_id=1, reply=_reply()))

    assert result.ok is False
    assert result.user_unreachable is False


def test_a_timeout_is_transient_and_nobody_is_deactivated() -> None:
    bot = _FakeBot(error=TimedOut())
    result = _run(_notifier(bot).send(chat_id=1, reply=_reply()))

    assert result.ok is False
    assert result.user_unreachable is False
    assert result.configuration_error is False


def test_a_rate_limit_is_transient() -> None:
    """RetryAfter must never deactivate anybody: it is Telegram asking
    us to slow down, which is a statement about our sending rate and not
    about this user."""
    bot = _FakeBot(error=RetryAfter(30))
    result = _run(_notifier(bot).send(chat_id=1, reply=_reply()))

    assert result.user_unreachable is False


def test_a_bad_token_is_a_configuration_error_and_blames_nobody() -> None:
    """The distinction that prevents a catastrophe: a wrong token would
    otherwise fail for every user in sequence, and any code treating
    failure as "this chat is dead" would deactivate the entire table."""
    bot = _FakeBot(error=InvalidToken("bad"))
    result = _run(_notifier(bot).send(chat_id=1, reply=_reply()))

    assert result.configuration_error is True
    assert result.user_unreachable is False


def test_a_successful_send_reports_ok_with_no_error() -> None:
    bot = _FakeBot()
    result = _run(_notifier(bot).send(chat_id=99, reply=_reply()))

    assert result == SendResult(ok=True)
    assert bot.calls[0]["chat_id"] == 99
    assert bot.calls[0]["text"] == "hello"


def test_the_keyboard_reaches_telegram_and_previews_are_disabled() -> None:
    bot = _FakeBot()
    _run(_notifier(bot).send(chat_id=1, reply=_reply()))

    call = bot.calls[0]
    assert call["reply_markup"] is not None
    # A job link would otherwise render a preview card under every
    # notification, which doubles the height of the message.
    assert call["disable_web_page_preview"] is True


def test_no_parse_mode_is_sent() -> None:
    """Asserted explicitly. Setting one later would make every job title
    containing Markdown punctuation a delivery failure."""
    bot = _FakeBot()
    _run(_notifier(bot).send(chat_id=1, reply=_reply()))

    assert "parse_mode" not in bot.calls[0]


# --- construction --------------------------------------------------------


def test_an_unconfigured_token_fails_loudly_at_construction(monkeypatch) -> None:
    """Rather than constructing a Bot with an empty string and failing
    later, per message, with an error that looks like a network fault."""
    monkeypatch.setattr(telegram_integration.settings, "telegram_bot_token", "")

    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        TelegramNotifier()


def test_the_context_manager_initialises_and_shuts_the_bot_down() -> None:
    bot = _FakeBot()
    notifier = _notifier(bot)

    async def scenario():
        async with notifier:
            pass

    _run(scenario())

    assert bot.initialised is True
    assert bot.shut_down is True
