"""Drive the whole onboarding flow against the real database, no Telegram.

Every message the bot would send is printed. Nothing touches the
Telegram API, so this can be run before the bot is even started.

    python -m scripts.onboarding_dryrun

It uses a fake Telegram ID in the 9_000_000_000 range, well outside
real ones, and deletes the user again at the end unless you pass
--keep.
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
from app.db.models.user import User
from app.db.session import dispose_engine, init_engine, session_scope
from app.services.cv_intake import CVIntakeService
from app.services.onboarding import OnboardingService

FAKE_TELEGRAM_ID = 9_000_000_001

# A minimal but genuinely valid PDF, so the magic-byte check passes.
FAKE_PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"


def show(label: str, reply) -> None:
    print(f"\n--- {label} ---")
    print(reply.text)
    for row in reply.buttons:
        print("   [" + "] [".join(button.label for button in row) + "]")


async def run(keep: bool) -> None:
    engine = init_engine()
    if engine is None:
        print("DATABASE_URL is not configured. Set it in your .env file.")
        return

    try:
        async with session_scope() as session:
            service = OnboardingService(session)

            show(
                "/start",
                await service.start(FAKE_TELEGRAM_ID, "dryrun", "Dry Run"),
            )

            async def download() -> bytes:
                return FAKE_PDF

            show(
                "upload cv.pdf",
                (
                    await service.handle_document(
                        telegram_id=FAKE_TELEGRAM_ID,
                        file_name="cv.pdf",
                        size_bytes=len(FAKE_PDF),
                        telegram_file_id="dryrun-file-id",
                        download=download,
                    )
                ).reply,
            )

            show(
                "roles",
                await service.handle_text(
                    FAKE_TELEGRAM_ID, "Backend Engineer, ML Engineer"
                ),
            )
            show(
                "locations",
                await service.handle_text(FAKE_TELEGRAM_ID, "Delhi, Noida"),
            )
            show(
                "remote tap",
                await service.handle_callback(FAKE_TELEGRAM_ID, "onb:remote:no"),
            )
            show(
                "experience tap",
                await service.handle_callback(FAKE_TELEGRAM_ID, "onb:exp:1-3"),
            )
            show(
                "threshold tap",
                await service.handle_callback(
                    FAKE_TELEGRAM_ID, "onb:threshold:0.7"
                ),
            )
            show("/status", await service.status(FAKE_TELEGRAM_ID))

            # Out-of-order taps must be rejected, not applied.
            show(
                "stale button (expected: refusal)",
                await service.handle_callback(FAKE_TELEGRAM_ID, "onb:exp:5-8"),
            )

        if not keep:
            async with session_scope() as session:
                user = await session.scalar(
                    select(User).where(User.telegram_id == FAKE_TELEGRAM_ID)
                )

                stored_paths: list[str] = []
                if user is not None:
                    cvs = await session.scalars(
                        select(CV).where(CV.user_id == user.id)
                    )
                    stored_paths = [cv.storage_path for cv in cvs if cv.storage_path]
                    user_id = user.id

                await session.execute(
                    delete(User).where(User.telegram_id == FAKE_TELEGRAM_ID)
                )

            # Outside the session: the row is already committed gone, and
            # a failed file delete should not roll back the database change.
            intake = CVIntakeService()
            for path in stored_paths:
                intake.delete(user_id, path)

            print("\nTest user removed. Pass --keep to leave it in place.")
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
