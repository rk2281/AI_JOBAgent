"""Which callback handler receives which tap.

This file exists because of a bug that produced no error.

`CallbackQueryHandler(onboarding.button_callback)` was registered with
no `pattern`, and such a handler matches EVERY callback query in the
application. That was harmless while onboarding owned the only buttons
in the project. Once a notification carries `fb:interested:42`, the
first registered handler wins, so every feedback tap would have gone to
`OnboardingService.handle_callback` -- which parses it, fails its
`parts[0] != CALLBACK_PREFIX` check, logs "Unrecognised callback data"
and tells the user the button expired.

No exception. No crash. Just a plausible-looking reply and feedback
silently never reaching the database, which would have been diagnosed
by staring at the feedback code -- the one part that was fine.

The tests below assert routing through the handlers' OWN
`check_update`, not through a re-implementation of the pattern match.
Asserting `re.match(pattern, data)` would test a regex this file wrote,
and pass just as happily if the pattern were never attached to a
handler at all -- which is exactly the failure being guarded against.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from telegram import CallbackQuery, Update, User
from telegram.ext import CallbackQueryHandler

from app.bot.handlers import (
    FEEDBACK_CALLBACK_PATTERN,
    ONBOARDING_CALLBACK_PATTERN,
    PREFERENCES_CALLBACK_PATTERN,
    register_handlers,
)
from app.bot.handlers import common, feedback, onboarding, preferences


def _run(coro):
    return asyncio.run(coro)


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


def _callback_handlers() -> list[CallbackQueryHandler]:
    return [
        handler
        for handler in _registered().handlers
        if isinstance(handler, CallbackQueryHandler)
    ]


def _update(data: str) -> Update:
    """A REAL telegram.Update carrying a real CallbackQuery.

    Not a stand-in object. CallbackQueryHandler.check_update() begins
    with `isinstance(update, Update)` and returns None for anything
    else -- so a duck-typed fake makes EVERY handler report the same
    non-match, the walk below picks the first one every time, and the
    file passes while asserting nothing. That is how the first draft of
    this test "passed" the routing it was written to check.
    """
    return Update(
        update_id=1,
        callback_query=CallbackQuery(
            id="1",
            from_user=User(id=555, first_name="Test", is_bot=False),
            chat_instance="1",
            data=data,
        ),
    )


def _receiver(data: str):
    """Which handler actually gets this tap, walking them in order.

    python-telegram-bot stops at the first match, so the FIRST handler
    whose check_update passes is the one that would run. That ordering
    is the whole subject of this file.

    Truthiness, not `is not False`: check_update returns a re.Match on a
    pattern hit, True for a handler with no pattern, and False on a
    miss. All three have to be read the same way the library reads them.
    """
    for handler in _callback_handlers():
        if handler.check_update(_update(data)):
            return handler.callback
    return None


# --- the bug ------------------------------------------------------------


def test_a_feedback_tap_reaches_the_feedback_handler() -> None:
    """Before Day 11 this reached onboarding.button_callback."""
    assert _receiver("fb:interested:42") is feedback.feedback_callback


def test_every_feedback_action_routes_to_the_feedback_handler() -> None:
    for data in ("fb:interested:1", "fb:saved:1", "fb:not_relevant:1"):
        assert _receiver(data) is feedback.feedback_callback


def test_onboarding_callbacks_still_reach_onboarding() -> None:
    """The regression that would matter most: fixing the routing must
    not break the flow that already worked."""
    for data in (
        "onb:remote:yes",
        "onb:remote:no",
        "onb:exp:0-1",
        "onb:threshold:0.7",
    ):
        assert _receiver(data) is onboarding.button_callback


def test_the_onboarding_handler_no_longer_matches_everything() -> None:
    """The assertion that would have caught the original bug.

    Asserted against the handler itself rather than the pattern string,
    so it fails if the pattern is removed, is attached to the wrong
    handler, or never reaches the Application.
    """
    onboarding_handler = _callback_handlers()[0]

    assert onboarding_handler.callback is onboarding.button_callback
    assert not onboarding_handler.check_update(_update("fb:interested:42"))
    assert onboarding_handler.check_update(_update("onb:remote:yes"))


def test_the_feedback_handler_declines_onboarding_callbacks() -> None:
    """The same rule in the other direction, so neither handler can grow
    into a catch-all later."""
    feedback_handler = _callback_handlers()[1]

    assert feedback_handler.callback is feedback.feedback_callback
    assert not feedback_handler.check_update(_update("onb:remote:yes"))


def test_a_preferences_tap_reaches_the_preferences_handler() -> None:
    assert _receiver("pref:roles:menu") is preferences.preferences_callback


def test_every_preferences_field_routes_to_the_preferences_handler() -> None:
    for data in (
        "pref:roles:menu",
        "pref:locations:menu",
        "pref:experience:menu",
        "pref:experience:1-3",
        "pref:threshold:menu",
        "pref:threshold:0.7",
    ):
        assert _receiver(data) is preferences.preferences_callback


def test_the_preferences_handler_declines_onboarding_and_feedback_callbacks() -> None:
    """The third prefix added after Day 11 must follow the same rule as
    the first two: declare what it accepts, decline everything else."""
    preferences_handler = _callback_handlers()[2]

    assert preferences_handler.callback is preferences.preferences_callback
    assert not preferences_handler.check_update(_update("onb:remote:yes"))
    assert not preferences_handler.check_update(_update("fb:interested:42"))
    assert preferences_handler.check_update(_update("pref:threshold:0.7"))


# --- the hole the fix opened, and its net --------------------------------


def test_an_unrecognised_callback_still_reaches_something() -> None:
    """Giving both handlers a pattern fixed the routing and opened a
    hole: data matching neither prefix now matches nothing, and an
    unanswered callback query leaves Telegram's spinner turning forever
    -- indistinguishable, to a user, from a bot that has died."""
    assert _receiver("something:else:entirely") is common.unknown_callback
    assert _receiver("onboarding-without-a-colon") is common.unknown_callback
    assert _receiver("fbsomething") is common.unknown_callback


def test_empty_callback_data_matches_every_handler_regardless_of_pattern() -> None:
    """A python-telegram-bot behaviour, verified in its source, not assumed.

    CallbackQueryHandler.check_update ends:

        if callback_data:      ... pattern matching ...
        elif game_short_name:  ... game pattern matching ...
        else:                  return True

    So a CallbackQuery carrying NEITHER data nor a game_short_name
    matches every handler, pattern or not, and the first registered one
    wins. The patterns therefore do not partition the space the way
    "each handler declares what it accepts" suggests, and anyone
    reasoning about routing from the registration list alone would get
    this case wrong.

    It is harmless here and is left alone rather than worked around.
    Telegram always sends one of the two, this bot creates no game
    buttons, and the handler that catches it -- onboarding -- already
    answers empty data with "That button has expired", so no button is
    left spinning. Pinned so that if it ever stops being harmless, it is
    a failure with an explanation attached rather than a rediscovery.
    """
    assert _receiver("") is onboarding.button_callback


def test_the_catch_all_is_registered_last() -> None:
    """Load-bearing ordering. It matches unconditionally, so registered
    anywhere but last it would swallow the handlers above it and
    reintroduce the original bug with a different callback on the end.
    """
    handlers = _callback_handlers()

    assert len(handlers) == 4
    assert handlers[-1].callback is common.unknown_callback
    assert handlers[-1].pattern is None


def test_the_unknown_callback_answers_the_query() -> None:
    """Answering is the entire job. A returned-but-unanswered callback
    is the spinning button."""
    answered = {}

    async def answer(text=None, show_alert=False):
        answered["text"] = text

    update = SimpleNamespace(callback_query=SimpleNamespace(data="x", answer=answer))

    _run(common.unknown_callback(update, None))

    assert answered["text"]


# --- what was deliberately not changed -----------------------------------


def test_the_text_catch_all_is_still_the_last_message_handler() -> None:
    """Registration order in this module is load-bearing and the Day 11
    change was additive. The catch-all text handler must still come
    after the document and photo handlers, or it swallows them."""
    from telegram.ext import MessageHandler

    message_handlers = [
        handler
        for handler in _registered().handlers
        if isinstance(handler, MessageHandler)
    ]

    assert message_handlers[-1].callback is onboarding.text_message


def test_the_error_handler_is_still_registered() -> None:
    assert _registered().error_handlers == [common.error_handler]


def test_the_patterns_are_built_from_the_services_own_prefixes() -> None:
    """Written out as literals, a prefix renamed in one place would leave
    a handler quietly matching nothing -- which is not an error, just a
    handler that never fires. The same silent shape as the bug above."""
    from app.services.notification_message import CALLBACK_PREFIX as FB
    from app.services.onboarding import CALLBACK_PREFIX as ONB
    from app.services.preferences import CALLBACK_PREFIX as PREF

    assert ONBOARDING_CALLBACK_PATTERN == rf"^{ONB}:"
    assert FEEDBACK_CALLBACK_PATTERN == rf"^{FB}:"
    assert PREFERENCES_CALLBACK_PATTERN == rf"^{PREF}:"
    assert len({ONB, FB, PREF}) == 3, "all three prefixes must be distinguishable"
