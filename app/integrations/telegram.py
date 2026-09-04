"""Outbound Telegram messages, and the one fact that makes them dangerous.

THE BOT TOKEN IS IN THE URL PATH

Every Bot API call this module makes is

    https://api.telegram.org/bot<TOKEN>/sendMessage

so the URL *is* a credential, exactly as Adzuna's is. That incident is
the canonical one in this project: `app_id` and `app_key` are query
parameters, httpx's HTTPStatusError formats the full URL into its own
string form, and INFO-level request logging therefore printed both keys
on every ingestion run for months. Nobody had logged a secret. They had
logged a URL.

The credential has simply moved one URL component over. Everything that
made Adzuna leak applies here unchanged:

  * `logger.error("send failed: %s", exc)` on a NetworkError whose
    httpx cause carries the URL writes the token to every log sink.
  * `str(exc)` stored in `notifications.error_message` writes the token
    into the database PERMANENTLY, to be read back weeks later by a
    human with no idea they are looking at a live credential.

So this module never formats an exception, never logs a request, a
response or a URL, and exports exactly one way to describe a failure:
describe_telegram_error(), which reads an exception's CLASS and, for
API-level errors, the API's own `message` field -- the same method
app/integrations/http_errors.py uses for httpx and _format_errors() in
gemini.py uses for Gemini. An error object is not automatically safe to
format just because it is an error.

_redact() is the belt to that braces. It replaces the configured token
with a marker in anything on its way to a log or the database, so that
a message from some path nobody anticipated still cannot carry it. It
reads the token in order to remove it and never prints it.

WHY A RESULT AND NOT AN EXCEPTION

send() returns a SendResult instead of raising, because the caller's
job is to record an outcome per recommendation and carry on. One
undeliverable job must not end a run, and a `try` around every call
site would be the same logic written once per caller with a chance to
forget it once.

THREE OUTCOMES, NOT TWO, AND THE THIRD IS THE POINT

ok / failed is not enough to act correctly:

  * `user_unreachable` -- the person blocked the bot, or the chat is
    gone. Retrying every remaining recommendation for them is pointless
    and looks like abuse. The service deactivates the user instead.
  * `configuration_error` -- the TOKEN is wrong. Nothing about that is
    the user's fault and deactivating people because of it would be a
    catastrophe with no error message attached. It aborts the stage.
  * everything else is transient and stays retryable, with no attempt
    ceiling anywhere: a Telegram outage must not permanently cost a
    user a job.
"""

from __future__ import annotations

from dataclasses import dataclass

from telegram import Bot
from telegram.error import (
    BadRequest,
    ChatMigrated,
    Conflict,
    Forbidden,
    InvalidToken,
    NetworkError,
    RetryAfter,
    TelegramError,
)

from app.bot.rendering import to_markup
from app.core.config import settings
from app.services.replies import BotReply

# Substrings that mean "this chat will never accept a message again",
# as opposed to "not right now". Telegram reports both as BadRequest,
# so the class alone cannot separate them and the text has to be read.
#
# Matched case-insensitively against the API's own `message`, which is
# the API's `description` field -- never against str(exc), which is
# where a URL could be.
_UNREACHABLE_MESSAGES = (
    "chat not found",
    "user is deactivated",
    "bot was blocked by the user",
    "peer_id_invalid",
)

_REDACTED = "***"


def _redact(text: str) -> str:
    """Remove the bot token from a string headed for a log or the database.

    A safety net under describe_telegram_error(), not a substitute for
    it. Everything this project writes about a Telegram failure is
    already built from an exception's class and the API's description,
    neither of which contains the token -- but "neither of which
    contains the token" is a claim about a third-party library's
    formatting that will be true until some version it is not, and the
    cost of being wrong once is a permanent credential in a database
    column.

    Reads settings.telegram_bot_token in order to remove it. It is
    never printed, never logged and never returned.
    """
    token = settings.telegram_bot_token
    if token and token in text:
        return text.replace(token, _REDACTED)
    return text


# The ONLY exception classes whose `.message` may be reported.
#
# An allowlist, not a blocklist, and the direction is the whole point.
# python-telegram-bot builds these five from the API's own JSON
# `description` field -- "Bad Request: chat not found" -- which Telegram
# writes about the request and which therefore cannot contain the URL.
#
# NetworkError is the counter-example and the reason this list exists.
# PTB constructs it by wrapping the underlying transport failure, so its
# `.message` is httpx's text -- INCLUDING the request URL, which for the
# Bot API contains the token. The first version of this function
# reported `.message` for every TelegramError and leaked the token
# through exactly that class. A test caught it: the assumption "a
# TelegramError's message comes from the API" was wrong for the one
# subclass where it matters most.
#
# AND THE HIERARCHY IS NOT WHAT IT LOOKS LIKE. Verified, not assumed:
#
#     TelegramError
#       +- NetworkError
#       |    +- BadRequest      <- an API error under the transport one
#       |    +- TimedOut
#       +- Forbidden, InvalidToken, RetryAfter, ChatMigrated, Conflict
#
# BadRequest is a NetworkError subclass. So "handle NetworkError first,
# since nothing safe inherits from it" -- which is what the fix for the
# leak originally said -- routes every BadRequest into the transport
# branch and throws away the API description that says WHY the request
# was rejected. That was the second bug in this function, caught by the
# same test file as the first.
#
# Hence: the allowlist is consulted FIRST, and it is an allowlist of
# concrete classes. Listing the SAFE ones rather than the unsafe ones
# makes the fail-closed direction structural -- a future PTB exception
# that embeds a URL falls through to its class name automatically, where
# a blocklist would let it leak by default because nobody adds a vendor
# exception to a blocklist they do not know exists. Same reasoning as
# config.py enumerating the DISABLED tracing values rather than the
# enabled ones.
_MESSAGE_SAFE_ERRORS = (BadRequest, Forbidden, ChatMigrated, RetryAfter, Conflict)


def describe_telegram_error(error: Exception) -> str:
    """Describe a Telegram failure without ever quoting a URL.

    Deliberately returns less information than the exception contains.
    The discarded part is anything httpx built, and that is the part
    that is unsafe -- see the module docstring.

    Four tiers, most restrictive first:

      InvalidToken   -> a fixed string. Its own message can quote the
                        token it rejected.
      an API error   -> class plus the API's description, redacted.
                        These five and no others.
      NetworkError   -> class name only, exactly as
                        describe_http_error() treats an httpx
                        TransportError. This is the transport tier and
                        the one that carries URLs.
      anything else  -> class name only.

    Only the second tier can emit a message, and only for five named
    classes. Everything else, known or not, is reported by its class.
    """
    if isinstance(error, InvalidToken):
        return "invalid bot token"

    # BEFORE the NetworkError check, because BadRequest is a
    # NetworkError subclass -- see the comment on _MESSAGE_SAFE_ERRORS.
    # These five carry the API's own `description`, which Telegram wrote
    # about the request and which cannot contain the URL.
    if isinstance(error, _MESSAGE_SAFE_ERRORS):
        message = _redact(error.message or "")
        return f"{type(error).__name__}: {message}" if message else type(error).__name__

    if isinstance(error, NetworkError):
        return f"connection failed ({type(error).__name__})"

    if isinstance(error, TelegramError):
        # A TelegramError this function has never been told is safe.
        # Reported by class alone rather than guessed about.
        return f"telegram error ({type(error).__name__})"

    return f"unexpected error ({type(error).__name__})"


def _is_unreachable(error: Exception) -> bool:
    """Is this chat permanently undeliverable, rather than failing today?

    Forbidden is unambiguous: the bot was blocked, or the account is
    gone. BadRequest is not -- it covers a malformed message just as
    happily as a missing chat -- so it qualifies only when the API's
    own description names one of the known permanent causes.

    Erring toward "transient" is the safe direction. A transient error
    misread as permanent deactivates a real user and silently stops
    every future notification for them; a permanent error misread as
    transient costs one wasted API call on the next run.
    """
    if isinstance(error, Forbidden):
        return True
    if isinstance(error, BadRequest):
        message = (error.message or "").lower()
        return any(needle in message for needle in _UNREACHABLE_MESSAGES)
    return False


@dataclass(frozen=True)
class SendResult:
    """What happened to one message. Never carries an exception object."""

    ok: bool
    error: str | None = None

    # The chat is permanently undeliverable. The caller deactivates the
    # user rather than working through their remaining recommendations.
    user_unreachable: bool = False

    # OUR credentials are wrong. Nothing to do with this user; the
    # caller stops the whole stage instead of blaming anybody.
    configuration_error: bool = False


class TelegramNotifier:
    """Sends one message at a time, and reports what happened.

    An async context manager because python-telegram-bot's Bot has to
    be initialised before use and shut down after. Opened around a
    delivery loop rather than per message: the alternative re-runs
    getMe on every recommendation.

    Deliberately NOT the bot Application. The Application is a polling
    receiver with handlers, a job queue and an update loop; a scheduled
    run needs to send and exit, and standing up a receiver in order to
    push one message would put a second consumer of the same token on
    the same account as the running bot.
    """

    def __init__(self, token: str | None = None) -> None:
        resolved = token if token is not None else settings.telegram_bot_token
        if not resolved:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")
        self._bot = Bot(resolved)

    async def __aenter__(self) -> TelegramNotifier:
        await self._bot.initialize()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self._bot.shutdown()

    async def send(self, *, chat_id: int, reply: BotReply) -> SendResult:
        """Deliver one BotReply. Returns rather than raises.

        The keyboard is built by app.bot.rendering.to_markup, the same
        function every handler uses, rather than by a second conversion
        written here. rendering.py's docstring calls itself "the only
        place that knows both sides", and a second copy of that
        conversion would be a second thing to keep in step the first
        time a button gains a field.
        """
        try:
            await self._bot.send_message(
                chat_id=chat_id,
                text=reply.text,
                reply_markup=to_markup(reply),
                disable_web_page_preview=True,
            )
        except InvalidToken as error:
            return SendResult(
                ok=False,
                error=describe_telegram_error(error),
                configuration_error=True,
            )
        except Exception as error:  # noqa: BLE001 - classified, never re-raised
            return SendResult(
                ok=False,
                error=describe_telegram_error(error),
                user_unreachable=_is_unreachable(error),
            )

        return SendResult(ok=True)
