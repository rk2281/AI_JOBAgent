"""Prove the harness is connected before any other test trusts it.

A separate module because pytest imports `conftest.py` but does not
collect tests from it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import text

from app.db.session import session_scope


def test_the_harness_is_actually_connected(
    run_with_database: Callable[[Callable[[], Awaitable[Any]]], Any],
) -> None:
    """A wrong TEST_DATABASE_URL should fail here, in these words.

    Without this it would surface as a confusing failure somewhere in
    test three. pgvector is asserted for the same reason: every
    semantic test below is meaningless without it, and "extension not
    installed" should be said once, clearly.
    """

    async def body() -> tuple[str, str | None]:
        async with session_scope() as session:
            version = (await session.execute(text("SHOW server_version"))).scalar_one()
            vector = (
                await session.execute(
                    text(
                        "SELECT extversion FROM pg_extension "
                        "WHERE extname = 'vector'"
                    )
                )
            ).scalar_one_or_none()
        return version, vector

    server_version, vector_version = run_with_database(body)

    assert server_version, "PostgreSQL did not report a version"
    assert vector_version is not None, (
        "The pgvector extension is not installed in TEST_DATABASE_URL. "
        "Run: CREATE EXTENSION IF NOT EXISTS vector;"
    )
