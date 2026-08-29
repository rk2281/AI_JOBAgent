"""Exercise onboarding edge cases against the real database, no Telegram.

scripts/onboarding_dryrun.py covers the happy path end to end. This
script covers the branches that never occur on a clean run: restart
resilience for an already-complete user, and the three ways a CV
upload can be rejected. Nothing here touches the Telegram API.

    python -m scripts.onboarding_edgecases_dryrun

Uses a fake Telegram ID in the 9_000_000_000 range, well outside real
ones, and deletes the user and their stored CV again at the end unless
--keep is passed.
"""

import argparse
import asyncio
import sys

if sys.platform == "win32":
    # psycopg's async driver needs a selector event loop; Windows defaults
    # to the proactor loop, which it can't use.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import delete, select

from app.db.models.cv import CV
from app.db.models.user import OnboardingState, User
from app.db.repositories.cv import CVRepository
from app.db.repositories.user import UserRepository
from app.db.session import dispose_engine, init_engine, session_scope
from app.services.cv_intake import CVIntakeService
from app.services.onboarding import OnboardingService

FAKE_TELEGRAM_ID = 9_000_000_002

# A minimal but genuinely valid PDF, so the magic-byte check passes.
REAL_PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"

# Correct extension, wrong content. The extension check passes; the
# magic-byte check must not.
FAKE_PDF_BYTES = b"this is plain text pretending to be a pdf"


def show(label: str, reply) -> None:
    print(f"\n--- {label} ---")
    print(reply.text)
    for row in reply.buttons:
        print("   [" + "] [".join(button.label for button in row) + "]")


async def get_state(user_id: int) -> OnboardingState:
    async with session_scope() as session:
        user = await session.get(User, user_id)
        assert user is not None
        return OnboardingState(user.onboarding_state)


async def get_cv_count(user_id: int) -> int:
    async with session_scope() as session:
        return await CVRepository(session).count_for_user(user_id)


async def complete_onboarding(service: OnboardingService) -> None:
    """Drive a fresh user to COMPLETE with one genuine CV upload."""

    async def download() -> bytes:
        return REAL_PDF

    await service.start(FAKE_TELEGRAM_ID, "edgecases", "Edge Cases")
    await service.handle_document(
        telegram_id=FAKE_TELEGRAM_ID,
        file_name="cv.pdf",
        size_bytes=len(REAL_PDF),
        telegram_file_id="edgecase-file-id",
        download=download,
    )
    await service.handle_text(FAKE_TELEGRAM_ID, "Backend Engineer")
    await service.handle_text(FAKE_TELEGRAM_ID, "Delhi")
    await service.handle_callback(FAKE_TELEGRAM_ID, "onb:remote:no")
    await service.handle_callback(FAKE_TELEGRAM_ID, "onb:exp:1-3")
    await service.handle_callback(FAKE_TELEGRAM_ID, "onb:threshold:0.7")


async def run(keep: bool) -> None:
    engine = init_engine()
    if engine is None:
        print("DATABASE_URL is not configured. Set it in your .env file.")
        return

    try:
        async with session_scope() as session:
            user_repo = UserRepository(session)
            existing = await user_repo.get_by_telegram_id(FAKE_TELEGRAM_ID)
            if existing is not None:
                print(
                    f"Fake user telegram_id={FAKE_TELEGRAM_ID} already exists "
                    f"(id={existing.id}). Run with a clean database or remove "
                    "it first."
                )
                return

        async with session_scope() as session:
            service = OnboardingService(session)
            await complete_onboarding(service)

        user_id_holder: dict[str, int] = {}
        async with session_scope() as session:
            user = await UserRepository(session).get_by_telegram_id(
                FAKE_TELEGRAM_ID
            )
            assert user is not None
            user_id_holder["id"] = user.id

        user_id = user_id_holder["id"]

        state = await get_state(user_id)
        cv_count = await get_cv_count(user_id)
        print(
            f"\nSetup complete. onboarding_state={state.value!r} "
            f"cv_count={cv_count}"
        )
        assert state is OnboardingState.COMPLETE
        assert cv_count == 1

        # -- 1. plain text while COMPLETE -------------------------------
        async with session_scope() as session:
            show(
                "text 'hello' while COMPLETE (expected: you're all set up)",
                await OnboardingService(session).handle_text(
                    FAKE_TELEGRAM_ID, "hello"
                ),
            )

        # -- 2. /restart on an already-complete user ---------------------
        async with session_scope() as session:
            show(
                "/restart on a complete user",
                await OnboardingService(session).restart(FAKE_TELEGRAM_ID),
            )

        state_after_restart = await get_state(user_id)
        cv_count_after_restart = await get_cv_count(user_id)
        print(
            f"\nAfter /restart: onboarding_state="
            f"{state_after_restart.value!r} cv_count={cv_count_after_restart}"
        )
        assert state_after_restart is OnboardingState.AWAITING_CV, (
            "BUG: /restart did not return the user to AWAITING_CV "
            f"(got {state_after_restart.value!r})"
        )
        assert cv_count_after_restart == 1, (
            "BUG: /restart deleted the existing CV row "
            f"(cv_count went from 1 to {cv_count_after_restart})"
        )
        print("OK: /restart re-asked for a CV and kept the existing CV row.")

        # -- 3. plain text while AWAITING_CV -----------------------------
        async with session_scope() as session:
            show(
                "text 'hello' while AWAITING_CV (expected: need a file)",
                await OnboardingService(session).handle_text(
                    FAKE_TELEGRAM_ID, "hello"
                ),
            )

        # -- 4. uploading a .txt file -------------------------------------
        async def download_txt() -> bytes:
            raise AssertionError(
                "download() must not be called — the extension check "
                "should reject this before any bytes are fetched"
            )

        async with session_scope() as session:
            show(
                "upload notes.txt (expected: extension rejection)",
                (
                    await OnboardingService(session).handle_document(
                        telegram_id=FAKE_TELEGRAM_ID,
                        file_name="notes.txt",
                        size_bytes=100,
                        telegram_file_id="edgecase-txt-id",
                        download=download_txt,
                    )
                ).reply,
            )

        cv_count_after_txt = await get_cv_count(user_id)
        assert cv_count_after_txt == 1, (
            "BUG: a .txt upload created a cvs row "
            f"(cv_count went from 1 to {cv_count_after_txt})"
        )
        print(f"OK: no cvs row created (cv_count={cv_count_after_txt}).")

        # -- 5. cv.pdf with non-PDF bytes ----------------------------------
        async def download_fake_pdf() -> bytes:
            return FAKE_PDF_BYTES

        async with session_scope() as session:
            show(
                "upload cv.pdf with non-PDF bytes (expected: magic-byte rejection)",
                (
                    await OnboardingService(session).handle_document(
                        telegram_id=FAKE_TELEGRAM_ID,
                        file_name="cv.pdf",
                        size_bytes=len(FAKE_PDF_BYTES),
                        telegram_file_id="edgecase-badpdf-id",
                        download=download_fake_pdf,
                    )
                ).reply,
            )

        cv_count_after_fake_pdf = await get_cv_count(user_id)
        assert cv_count_after_fake_pdf == 1, (
            "BUG: a fake-content cv.pdf created a cvs row "
            f"(cv_count went from 1 to {cv_count_after_fake_pdf})"
        )
        print(f"OK: no cvs row created (cv_count={cv_count_after_fake_pdf}).")

        print("\nAll edge cases behaved as expected.")

        if not keep:
            async with session_scope() as session:
                cvs = await session.scalars(
                    select(CV).where(CV.user_id == user_id)
                )
                stored_paths = [cv.storage_path for cv in cvs if cv.storage_path]

                await session.execute(
                    delete(User).where(User.telegram_id == FAKE_TELEGRAM_ID)
                )

            intake = CVIntakeService()
            for path in stored_paths:
                intake.delete(user_id, path)

            print("\nTest user and stored CV removed. Pass --keep to leave them.")
    finally:
        await dispose_engine()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--keep",
        action="store_true",
        help="leave the test user in the database",
    )
    arguments = parser.parse_args()

    asyncio.run(run(arguments.keep))


if __name__ == "__main__":
    main()
