from telegram.ext import Application

from app.bot.handlers import register_handlers
from app.core.config import settings


def create_bot_application() -> Application:
    if not settings.telegram_bot_token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not configured."
        )

    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .build()
    )

    register_handlers(application)

    return application