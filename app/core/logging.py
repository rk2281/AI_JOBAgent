import logging

from app.core.config import settings


def _quiet_http_client_loggers() -> None:
    """Silence httpx/httpcore's INFO-level request logging.

    httpx logs the full request URL at INFO, and every Telegram API
    URL embeds the bot token:

        POST https://api.telegram.org/bot<TOKEN>/getUpdates

    Polling emits one of these every ten seconds, so any log a person
    copies out of this terminal carries a live credential. That has
    happened four times on this project.

    WARNING rather than removing the handler: a genuine network
    failure still surfaces, and those messages carry the exception,
    not the URL. Set explicitly rather than raising the root level,
    because the application's own INFO lines are the useful ones.

    Idempotent: setLevel() on an already-WARNING logger is a no-op,
    so calling this again from setup_logging() after import already
    applied it changes nothing.
    """
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


# Applied at IMPORT time, not only inside setup_logging(). Every
# script under scripts/ talks to app.integrations directly and never
# runs app/main.py's lifespan, so setup_logging() never fires for
# them -- httpx would log every request URL at INFO with the
# interpreter's default logging config. That was harmless for
# google-genai, which sends its key as a header, and NOT harmless for
# Adzuna, whose app_id and app_key travel as URL query parameters: a
# plain `python -m scripts.ingest_jobs` would print both straight to
# the terminal the first time it made a request.
#
# Eight credential leaks on this project so far have all had the same
# shape -- a secret escaping through whatever handled it incidentally,
# never through something that printed it on purpose. A script
# forgetting to call setup_logging() is exactly that shape, so the
# guard against it cannot depend on every script remembering to ask
# for it.
_quiet_http_client_loggers()


def setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    _quiet_http_client_loggers()

