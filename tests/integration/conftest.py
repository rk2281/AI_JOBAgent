"""Harness for the tests that need a real PostgreSQL with pgvector.

Day 12 added these. Until now the entire suite ran with no database at
all, which is why `scoring_runs` could acquire a `CompileError` that
survived two parts and a commit (CLAUDE.md, "Open after Day 10 Part
3"), and why the ORM metadata could drift four objects away from the
migrated schema without anything noticing.

HOW TO RUN THEM

    createdb jobagent_test
    psql -d jobagent_test -c 'CREATE EXTENSION IF NOT EXISTS vector'
    $env:TEST_DATABASE_URL =
        "postgresql+psycopg://USER:PASS@localhost:5432/jobagent_test"
    python -m pytest tests/integration -v

WITHOUT `TEST_DATABASE_URL` THEY SKIP, AND THAT IS THE RISK

A skipped test reports the same green dot as a passing one. `pytest -q`
prints "s" rather than "." and almost nobody reads the difference, so
the honest reading of a default run is "the integration tests did not
run", not "the integration tests passed".
`test_the_harness_is_actually_connected` below is the answer: when the
variable IS set, it asserts the connection is real, so a misconfigured
URL fails loudly instead of silently skipping. There is no way to make
an ABSENT database loud from inside pytest, which is why
docs/TEST_RESULTS.md records the two figures separately.

THE DATABASE THIS POINTS AT WILL BE TRUNCATED

Every table in `Base.metadata` is emptied before each test. Point this
at a scratch database, never at the development one.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import pytest
from sqlalchemy import text

if sys.platform == "win32":
    # psycopg's async driver cannot use the ProactorEventLoop Windows
    # defaults to. Every script in this repo sets this before importing
    # anything from app.db (CLAUDE.md section 5); this fixture module
    # never did, because TEST_DATABASE_URL had apparently never been set
    # on Windows before -- every prior run skipped before asyncio.run()
    # ever executed. Must be set before the first event loop is created,
    # so it goes here, at import time, before app.db.session is imported
    # below.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import app.db.models  # noqa: F401  -- registers every table on Base.metadata
import app.services.notification_delivery as notification_delivery
from app.core.config import settings
from app.db.base import Base
from app.db.session import dispose_engine, init_engine, session_scope

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

T = TypeVar("T")

# Emptied before each test, in one statement. CASCADE handles the
# foreign keys; RESTART IDENTITY means a test can assert on id 1
# without depending on how many tests ran before it.
#
# The `import app.db.models` above is load-bearing: without it
# Base.metadata is EMPTY at import time and this joins to "", which
# PostgreSQL rejects with a syntax error rather than truncating
# nothing. Observed while writing this file.
_TABLES = ", ".join(sorted(Base.metadata.tables))

assert _TABLES, "No tables registered on Base.metadata"


@pytest.fixture(scope="session", autouse=True)
def _migrated_database() -> None:
    """Bring a scratch database to head, once, before anything runs.

    Migrations run through the alembic CLI in a subprocess rather than
    through `alembic.command` in-process, for one reason: it is the
    command a human runs. An in-process call can succeed against an
    env.py that the CLI would fail on -- different working directory,
    different config discovery, different event loop policy -- and the
    thing worth testing is the command in the README.

    This makes "the migrations apply to an empty database" a tested
    property. It was not one before Day 12; the development database
    was built incrementally over eleven days and no run had ever
    started from nothing.
    """
    if not TEST_DATABASE_URL:
        pytest.skip(
            "TEST_DATABASE_URL is not set; integration tests need a "
            "real PostgreSQL with the pgvector extension. See the "
            "docstring in tests/integration/conftest.py.",
            allow_module_level=True,
        )

    environment = dict(os.environ, DATABASE_URL=TEST_DATABASE_URL)

    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=environment,
        capture_output=True,
        text=True,
    )

    if completed.returncode != 0:
        # stdout AND stderr: alembic writes its progress to stderr, so
        # showing only stdout on failure usually shows nothing at all.
        pytest.fail(
            "alembic upgrade head failed against TEST_DATABASE_URL.\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )

    # The application's settings object is the one every service reads,
    # so pointing it here is what makes `session_scope()` inside the
    # services under test reach the scratch database. tests/conftest.py
    # deliberately pinned it at a dead port; this overrides that for
    # this directory only.
    settings.database_url = TEST_DATABASE_URL


@pytest.fixture(autouse=True)
def _no_real_telegram_notifier(monkeypatch: pytest.MonkeyPatch) -> None:
    """No integration test may ever open a socket to Telegram.

    deliver_notifications() (app/services/notification_delivery.py)
    builds a real TelegramNotifier whenever a caller doesn't pass one --
    that is the documented production default, not a bug. Two Day 16
    tests called _try_instant_recommendation, which has no way to pass a
    notifier through at all, and that default silently built a real
    TelegramNotifier against this conftest's fake token and made a real
    outbound call to api.telegram.org (InvalidToken, sent=0). Autouse so
    a future test cannot reintroduce the same mistake by omission.

    Replaces the class itself with one whose constructor raises, so a
    test relying on the production default fails immediately and
    loudly instead of quietly reaching the network. A test that needs
    to exercise real delivery overrides this again, in its own body,
    with its own fake -- see
    tests/integration/test_onboarding_completion_ordering.py.
    """

    class _RealTelegramNotifierIsForbiddenInTests:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError(
                "A real TelegramNotifier was about to be constructed "
                "during a test. Pass your own fake notifier, or patch "
                "notification_delivery.TelegramNotifier explicitly, "
                "instead of relying on deliver_notifications()'s "
                "production default."
            )

    monkeypatch.setattr(
        notification_delivery,
        "TelegramNotifier",
        _RealTelegramNotifierIsForbiddenInTests,
    )


@pytest.fixture()
def run_with_database() -> Callable[[Callable[[], Awaitable[T]]], T]:
    """Run one coroutine against a freshly truncated database.

    Returns a callable rather than yielding a session, because a test
    must do all of its async work inside a SINGLE `asyncio.run()`. The
    engine pools connections, and a connection created under one event
    loop and reused under the next raises deep inside asyncpg-style
    drivers with an error that points nowhere near the cause. Engine
    creation, truncation, the test body and disposal therefore all
    happen in one loop, here.

    `pytest-asyncio` is not installed and must not be (CLAUDE.md
    section 5), so this is the shape every async test in the repository
    uses -- a synchronous function driving a coroutine.
    """

    def run(body: Callable[[], Awaitable[T]]) -> T:
        async def entry() -> T:
            init_engine()
            try:
                async with session_scope() as session:
                    await session.execute(
                        text(f"TRUNCATE TABLE {_TABLES} RESTART IDENTITY CASCADE")
                    )
                return await body()
            finally:
                await dispose_engine()

        return asyncio.run(entry())

    return run

# The harness's own check lives in tests/integration/test_harness.py,
# not here: pytest imports conftest.py but does not COLLECT tests from
# it, so a check written here runs zero times while the run still looks
# clean.
