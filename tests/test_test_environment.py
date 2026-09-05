"""Guards on the test environment itself.

`tests/conftest.py` pins a hermetic environment before any `app.*`
import, so that the suite cannot reach the real Telegram bot, the real
database, or any real third-party credential. That protection is
invisible when it works, so this file makes it visible.

It is a separate module on purpose. pytest IMPORTS `conftest.py` but
does not COLLECT tests from it: written there, this check ran zero
times and the suite still reported a clean pass.
"""

import os

from tests.conftest import TEST_BOT_TOKEN, TEST_DATABASE_URL


def test_no_real_credentials_reached_the_test_process() -> None:
    """Fail if the suite is running against real configuration.

    Import `app.core.config` from a plugin, or from a `tests/__init__.py`
    that grows an import one day, and `Settings` is constructed before
    conftest runs. The environment assignments still succeed, the
    cached singleton ignores them, and every test goes green while
    polling the production bot again.

    This is the answer to "what would it look like if conftest silently
    did nothing".
    """
    from app.core.config import settings

    assert settings.telegram_bot_token == TEST_BOT_TOKEN, (
        "The test process holds a Telegram token that is not the test "
        "token. app.core.config was imported before tests/conftest.py "
        "ran, so the real .env is in effect and this suite may be "
        "driving the live bot."
    )
    assert settings.telegram_mode == "disabled"
    assert not settings.gemini_api_key
    assert not settings.adzuna_app_key

    # database_url is the ONE field the integration harness is allowed
    # to move: tests/integration/conftest.py points it at
    # TEST_DATABASE_URL so the services under test reach a real
    # PostgreSQL. Both values are therefore acceptable, and anything
    # else means the developer's own database is in play.
    #
    # Written as an assertion over two known values rather than dropped
    # from the guard, because "we stopped checking this field" and "the
    # harness moved it on purpose" look identical in a diff. Asserting
    # equality alone failed the first time the whole suite ran with
    # TEST_DATABASE_URL set, which is how the interaction was found.
    permitted = {TEST_DATABASE_URL, os.environ.get("TEST_DATABASE_URL", "")} - {""}
    assert settings.database_url in permitted, (
        "The test process is pointed at a database that is neither the "
        "dead placeholder nor TEST_DATABASE_URL. Tests would run "
        "against, and truncate, whatever this is."
    )


def test_the_app_lifespan_starts_no_bot_under_test() -> None:
    """The lifespan must not construct a Telegram Application here.

    `app.main.telegram_application` stays None for any mode other than
    `polling`. Asserting it after a full client lifecycle is what
    separates "no bot was started" from "a bot was started and we did
    not look".
    """
    from fastapi.testclient import TestClient

    import app.main as main

    with TestClient(main.app) as client:
        assert client.get("/health").status_code == 200

    assert main.telegram_application is None


def test_an_unrecognised_telegram_mode_is_fatal() -> None:
    """A typo in TELEGRAM_MODE must not start a silently botless API."""
    import pytest

    from app.core.config import Settings

    with pytest.raises(ValueError, match="not recognised"):
        Settings(telegram_mode="poling")


def test_webhook_mode_is_rejected_by_name() -> None:
    """`webhook` is unimplemented, not a typo, and says so."""
    import pytest

    from app.core.config import Settings

    with pytest.raises(ValueError, match="not implemented"):
        Settings(telegram_mode="webhook")


def test_a_recognised_mode_is_normalised() -> None:
    assert Settings_mode(" Polling ") == "polling"
    assert Settings_mode("DISABLED") == "disabled"


def Settings_mode(value: str) -> str:
    from app.core.config import Settings

    return Settings(telegram_mode=value).telegram_mode
