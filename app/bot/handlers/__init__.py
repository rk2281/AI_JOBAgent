"""Handler registration.

Order matters. python-telegram-bot walks handlers in the order they
were added and stops at the first match, so the catch-all text handler
must come last or it will swallow everything.

THE DAY 11 CALLBACK BUG, AND WHY A PATTERN WAS THE FIX

`CallbackQueryHandler(onboarding.button_callback)` was registered with
no `pattern`, and a CallbackQueryHandler with no pattern matches EVERY
callback query in the application. That was invisible while onboarding
owned the only buttons in the project. The moment notifications carry
`fb:interested:123`, it stops being invisible: the first match wins, so
every feedback tap would have been routed into
`OnboardingService.handle_callback`, which would have parsed it, failed
its `parts[0] != CALLBACK_PREFIX` check, logged "Unrecognised callback
data" and told the user their button had expired.

Note the shape of that failure. No exception, no error, no crash --
just a reply that reads plausible, and feedback silently never reaching
the database. It would have been diagnosed as "feedback does not work"
with the feedback code, which was fine, as the prime suspect.

So each callback handler now declares what it accepts:

    ^onb:   onboarding
    ^fb:    feedback
    (none)  common.unknown_callback, registered last

The two prefixes are disjoint, so the ORDER of the first two carries no
meaning and neither depends on the other -- which is the point. The
third is a safety net and its position DOES matter: it matches
unconditionally and must therefore come after everything it is a net
for.

WHAT WAS DELIBERATELY NOT DONE

Nothing was reordered. The commands, the document, photo and text
handlers and the error handler sit exactly where they were, and the
catch-all `filters.TEXT & ~filters.COMMAND` handler is still last among
the message handlers. Registration order here is load-bearing and the
Day 11 change is additive: two `pattern=` arguments and two new
handlers appended.
"""

from __future__ import annotations

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from app.bot.handlers import common, feedback, onboarding, profile
from app.services.notification_message import CALLBACK_PREFIX as FEEDBACK_PREFIX
from app.services.onboarding import CALLBACK_PREFIX as ONBOARDING_PREFIX

# Built from the prefixes the services actually use rather than written
# out as literals, so a prefix renamed in one place cannot leave a
# handler quietly matching nothing. A pattern that matches nothing is
# not an error -- the handler simply never fires, which is the same
# silent shape as the bug this file's docstring describes.
ONBOARDING_CALLBACK_PATTERN = rf"^{ONBOARDING_PREFIX}:"
FEEDBACK_CALLBACK_PATTERN = rf"^{FEEDBACK_PREFIX}:"


def register_handlers(application: Application) -> None:
    application.add_handler(CommandHandler("start", onboarding.start_command))
    application.add_handler(CommandHandler("status", onboarding.status_command))
    application.add_handler(CommandHandler("restart", onboarding.restart_command))
    application.add_handler(CommandHandler("profile", profile.profile_command))
    application.add_handler(CommandHandler("update_cv", profile.update_cv_command))
    application.add_handler(CommandHandler("help", common.help_command))
    application.add_handler(CommandHandler("ping", common.ping_command))

    # Each declares what it accepts. See the module docstring: without
    # the pattern, this first one matched every callback in the app.
    application.add_handler(
        CallbackQueryHandler(
            onboarding.button_callback, pattern=ONBOARDING_CALLBACK_PATTERN
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            feedback.feedback_callback, pattern=FEEDBACK_CALLBACK_PATTERN
        )
    )
    # Last, and unconditional. Answers anything the two above declined,
    # so no button is ever left spinning.
    application.add_handler(CallbackQueryHandler(common.unknown_callback))

    application.add_handler(
        MessageHandler(filters.Document.ALL, onboarding.document_message)
    )
    application.add_handler(
        MessageHandler(filters.PHOTO, onboarding.photo_message)
    )

    # Catch-all for ordinary typing. ~filters.COMMAND keeps an unknown
    # command such as /foo out of the onboarding state machine, where it
    # would be stored as a target role.
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            onboarding.text_message,
        )
    )

    application.add_error_handler(common.error_handler)


__all__ = [
    "FEEDBACK_CALLBACK_PATTERN",
    "ONBOARDING_CALLBACK_PATTERN",
    "register_handlers",
]
