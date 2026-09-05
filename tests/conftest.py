"""Test-suite environment. Imported by pytest before any test module.

Why this file exists (Day 12)
-----------------------------
`tests/test_health.py` builds a `TestClient(app)` as a context manager,
which runs the FastAPI lifespan. Until Day 12 the lifespan read the
developer's real `.env`, found `TELEGRAM_MODE=polling` and a real
`TELEGRAM_BOT_TOKEN`, and therefore:

    Application.initialize()      -> live getMe   to api.telegram.org
    updater.start_polling()       -> live getUpdates loop

So three "smoke tests" authenticated as the production bot and began
consuming real updates. Telegram allows exactly one `getUpdates`
consumer per bot, so a `pytest` run raced the real bot: the loser got
409 Conflict, and any update the test process won was acknowledged and
discarded. A user's CV upload arriving during a test run could vanish.

The tests PASSED throughout, on every machine with a network and a
populated `.env`. That is CLAUDE.md section 0 exactly -- a success
status is not success -- which is why the fix is structural rather than
a `monkeypatch` in one file.

What was rejected
-----------------
* Detecting pytest inside `lifespan` and skipping the bot there. That
  makes the tested startup path differ from the shipped one, and the
  same branch would mask a real misconfiguration in production.
* Patching `test_health.py` alone. It leaves the landmine armed for the
  next person who writes a `TestClient` test.

How this works
--------------
pytest imports `conftest.py` before it imports any test module, and
pydantic-settings ranks OS environment variables ABOVE values read from
`.env`. Assigning here therefore wins over the developer's real file
without touching it. `app.core.config` must not be imported before this
runs -- `test_no_real_credentials_reached_the_test_process` below is
what tells you if that ever stops being true.
"""

import os

# --- Hermetic environment -------------------------------------------------
#
# Set BEFORE any `app.*` import anywhere in the suite. The token is
# syntactically shaped like a Telegram token so that anything parsing it
# behaves normally; it authenticates as nothing.

TEST_BOT_TOKEN = "100000000:TEST-TOKEN-NOT-A-REAL-CREDENTIAL"

# 127.0.0.1:1 refuses immediately rather than hanging. The readiness
# endpoint is SUPPOSED to reach a database and report what it found, so
# pointing it at a closed port exercises the degraded branch for real
# instead of stubbing the answer.
TEST_DATABASE_URL = (
    "postgresql+psycopg://testuser:testpass@127.0.0.1:1/does_not_exist"
)

os.environ["TELEGRAM_BOT_TOKEN"] = TEST_BOT_TOKEN
os.environ["TELEGRAM_MODE"] = "disabled"
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

# Third-party credentials. Blank, not fake: every call site already has
# to handle "not configured", and a fake key would instead produce a
# live request that fails with 401 -- a network round trip from a unit
# test, which is the thing this file exists to stop.
os.environ["GEMINI_API_KEY"] = ""
os.environ["ADZUNA_APP_ID"] = ""
os.environ["ADZUNA_APP_KEY"] = ""

# LangSmith. `assert_tracing_disabled()` fails closed on an unrecognised
# flag value, so leaving a developer's shell variable in place would
# make graph tests die confusingly. Explicitly off, both spellings.
for _var in (
    "LANGCHAIN_TRACING",
    "LANGCHAIN_TRACING_V2",
    "LANGSMITH_TRACING",
    "LANGCHAIN_API_KEY",
    "LANGSMITH_API_KEY",
    "LANGCHAIN_ENDPOINT",
    "LANGSMITH_ENDPOINT",
    "LANGCHAIN_PROJECT",
    "LANGSMITH_PROJECT",
):
    os.environ.pop(_var, None)

os.environ["LANGCHAIN_TRACING_V2"] = "false"

# The check that says the above actually happened lives in
# tests/test_test_environment.py, NOT here: pytest imports conftest.py
# but does not COLLECT tests from it. Written here first, the guard ran
# zero times and the suite still reported a clean pass -- the same
# shape of mistake the guard exists to catch, made while writing it.
