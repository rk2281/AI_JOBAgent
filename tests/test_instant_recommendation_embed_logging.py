"""_try_instant_recommendation's embed step -- hardened the same way as
score_and_notify_user's except block (see
tests/test_score_and_notify_user.py::
test_an_invalid_token_failure_never_leaks_the_token_into_logs).

Day 16 restructured _try_instant_recommendation around embed_cv_version.
Incident 13 (CLAUDE.md, Day 15) was exactly a psycopg connection failure
whose exception message carried a host, a username and a plaintext
password, surfaced through a traceback nobody expected to be
security-sensitive. embed_cv_version opens a database session, so its
failure path is exactly the shape that produced Incident 13. This test
proves the handler's except block cannot repeat it: no exc_info, no
str(exc), only user_id, version_id and type(exc).__name__ reach the log.

Pure asyncio, no database: embed_cv_version is monkeypatched to raise
directly, so what is under test is the except block in
app.bot.handlers.onboarding._try_instant_recommendation, not
embed_cv_version itself (covered elsewhere, including
tests/integration/test_cv_embedding_pipeline.py).

pytest-asyncio is not installed and must not be (CLAUDE.md section 5).
"""

from __future__ import annotations

import asyncio
import logging

import app.bot.handlers.onboarding as onboarding_handlers


class FakeConnectionError(Exception):
    """Shaped like a real psycopg connection failure, for this test only."""


def _run(coro):
    return asyncio.run(coro)


def test_an_embed_failure_never_leaks_connection_details_into_logs(
    monkeypatch, caplog
) -> None:
    fake_host = "ep-fake-host-12345.us-east-2.aws.neon.tech"
    fake_user = "fake_neon_user"
    fake_password = "fakeSuperSecretPassword123"

    async def raising_embed(version_id):
        raise FakeConnectionError(
            f'connection to server at "{fake_host}" (1.2.3.4), port 5432 '
            f'failed: FATAL:  password authentication failed for user '
            f'"{fake_user}" password="{fake_password}"'
        )

    monkeypatch.setattr(onboarding_handlers, "embed_cv_version", raising_embed)

    with caplog.at_level(logging.ERROR):
        _run(onboarding_handlers._try_instant_recommendation(user_id=13, version_id=20))

    for secret in (fake_host, fake_user, fake_password):
        assert secret not in caplog.text

    assert caplog.records, "expected the embed failure to be logged"
    for record in caplog.records:
        assert record.exc_info is None, (
            "exc_info must never be attached here -- it prints str(exc) "
            "verbatim via the traceback, regardless of what the log "
            "message itself says"
        )
        message = record.getMessage()
        for secret in (fake_host, fake_user, fake_password):
            assert secret not in message

    joined = " ".join(record.getMessage() for record in caplog.records)
    assert "user_id=13" in joined
    assert "version_id=20" in joined
    assert "FakeConnectionError" in joined
