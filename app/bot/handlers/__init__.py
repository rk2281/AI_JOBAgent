"""Handler registration.

Order matters. python-telegram-bot walks handlers in the order they
were added and stops at the first match, so the catch-all text handler
must come last or it will swallow everything.
"""

from __future__ import annotations

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from app.bot.handlers import common, onboarding, profile


def register_handlers(application: Application) -> None:
    application.add_handler(CommandHandler("start", onboarding.start_command))
    application.add_handler(CommandHandler("status", onboarding.status_command))
    application.add_handler(CommandHandler("restart", onboarding.restart_command))
    application.add_handler(CommandHandler("profile", profile.profile_command))
    application.add_handler(CommandHandler("update_cv", profile.update_cv_command))
    application.add_handler(CommandHandler("help", common.help_command))
    application.add_handler(CommandHandler("ping", common.ping_command))

    application.add_handler(CallbackQueryHandler(onboarding.button_callback))

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


__all__ = ["register_handlers"]
