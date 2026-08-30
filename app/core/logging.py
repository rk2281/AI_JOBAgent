import logging

from app.core.config import settings


def setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    # httpx logs the full request URL at INFO, and every Telegram API
    # URL embeds the bot token:
    #
    #     POST https://api.telegram.org/bot<TOKEN>/getUpdates
    #
    # Polling emits one of these every ten seconds, so any log a person
    # copies out of this terminal carries a live credential. That has
    # happened four times on this project.
    #
    # WARNING rather than removing the handler: a genuine network
    # failure still surfaces, and those messages carry the exception,
    # not the URL. Set explicitly rather than raising the root level,
    # because the application's own INFO lines are the useful ones.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

