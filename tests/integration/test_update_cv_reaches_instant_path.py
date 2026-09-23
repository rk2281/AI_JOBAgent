"""/update_cv reaches the same instant-recommendation path as a first
upload -- Stage 0's finding 1, proven rather than left as a read of the
code.

OnboardingService.handle_document has a dedicated branch for a COMPLETE
user replacing their CV (app/services/onboarding.py: "A returning user
replacing their CV, not onboarding"), and it returns stored=True with
the real user_id -- exactly the two conditions
app/bot/handlers/onboarding.py's document_message checks before
scheduling _extract_and_notify -> _try_instant_recommendation:

    if outcome.stored and outcome.user_id is not None:

This test proves that condition holds for a COMPLETE user. It does not
re-drive the Telegram handler itself -- there is no precedent anywhere
in this suite for mocking python-telegram-bot's Update/Bot objects --
and it does not call extract_cv or Gemini: that boundary is exactly
where DocumentOutcome.stored hands off to a background task, which is
the handler's job, not the service's.

Real PostgreSQL. cv_storage_dir is redirected to tmp_path for the
duration of this test: OnboardingService always builds its own
CVIntakeService() from settings.cv_storage_dir with no override, and
the untouched default is "storage/cvs" -- the real project directory.
Redirecting it here is what stops this test from writing a real file
there, the same reason tests/integration/conftest.py redirects
DATABASE_URL rather than trusting whatever a developer's shell has set.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.core.config import settings
from app.db.models.user import OnboardingState, User
from app.db.session import session_scope
from app.services.onboarding import OnboardingService

_REAL_PDF_MAGIC_BYTES = b"%PDF-1.7\n" + b"body" * 50


async def _make_complete_user(*, telegram_id: int) -> None:
    async with session_scope() as session:
        session.add(
            User(
                telegram_id=telegram_id,
                full_name="Returning Candidate",
                onboarding_state=OnboardingState.COMPLETE.value,
            )
        )


def test_a_complete_user_replacing_their_cv_is_stored_with_a_user_id(
    run_with_database: Callable[[Callable[[], Awaitable[Any]]], Any],
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "cv_storage_dir", str(tmp_path))

    async def body() -> Any:
        await _make_complete_user(telegram_id=930001)

        async def download() -> bytes:
            return _REAL_PDF_MAGIC_BYTES

        async with session_scope() as session:
            return await OnboardingService(session).handle_document(
                telegram_id=930001,
                file_name="new_cv.pdf",
                size_bytes=len(_REAL_PDF_MAGIC_BYTES),
                telegram_file_id="fake-file-id",
                download=download,
            )

    outcome = run_with_database(body)

    assert outcome.stored is True
    assert outcome.user_id is not None
    # The COMPLETE-branch's own wording (onboarding.py: "Your
    # preferences are unchanged"), so a future edit to the AWAITING_CV
    # branch's wording cannot make this test pass by accident.
    assert "unchanged" in outcome.reply.text
