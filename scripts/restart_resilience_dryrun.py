"""Prove that onboarding survives a process restart, no Telegram.

users.onboarding_state exists specifically so that a user mid-flow is
not stranded when the process restarts — see the design note at the
top of app/services/onboarding.py. This script is the only thing in
the repository that actually proves it: it advances a fake user to
AWAITING_ROLES, disposes the database engine entirely (there is no
closer stand-in for a process restart without actually restarting the
process), re-initialises it, and confirms that calling /start again
resumes at AWAITING_ROLES rather than re-asking for a CV or losing the
one already stored.

    python -m scripts.restart_resilience_dryrun

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
from app.db.session import dispose_engine, init_engine, session_scope
from app.services.cv_intake import CVIntakeService
from app.services.onboarding import OnboardingService

FAKE_TELEGRAM_ID = 9_000_000_003

REAL_PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"


def show(label: str, reply) -> None:
    print(f"\n--- {label} ---")
    print(reply.text)


async def get_state_and_cv_count(user_id: int) -> tuple[OnboardingState, int]:
    async with session_scope() as session:
        user = await session.get(User, user_id)
        assert user is not None
        count = await CVRepository(session).count_for_user(user_id)
        return OnboardingState(user.onboarding_state), count


async def run(keep: bool) -> None:
    engine = init_engine()
    if engine is None:
        print("DATABASE_URL is not configured. Set it in your .env file.")
        return

    async def download() -> bytes:
        return REAL_PDF

    # -- advance a fresh user to AWAITING_ROLES, with a real stored CV ---
    async with session_scope() as session:
        service = OnboardingService(session)

        show("/start", await service.start(FAKE_TELEGRAM_ID, "resilience", "R"))
        show(
            "upload cv.pdf",
            (
                await service.handle_document(
                    telegram_id=FAKE_TELEGRAM_ID,
                    file_name="cv.pdf",
                    size_bytes=len(REAL_PDF),
                    telegram_file_id="resilience-file-id",
                    download=download,
                )
            ).reply,
        )

    async with session_scope() as session:
        user = await session.execute(
            select(User).where(User.telegram_id == FAKE_TELEGRAM_ID)
        )
        user_id = user.scalar_one().id

    state_before, cv_count_before = await get_state_and_cv_count(user_id)
    print(
        f"\nBefore restart: onboarding_state={state_before.value!r} "
        f"cv_count={cv_count_before}"
    )
    assert state_before is OnboardingState.AWAITING_ROLES
    assert cv_count_before == 1

    # -- simulate a process restart: dispose the engine entirely, then
    # re-initialise it, exactly as app.main's lifespan does on shutdown
    # and the next startup would do. --------------------------------------
    await dispose_engine()
    print("\nEngine disposed — simulating a process restart.")

    engine = init_engine()
    assert engine is not None
    print("Engine re-initialised.")

    # -- call /start again, as a genuinely fresh process would on the
    # user's next message ---------------------------------------------
    async with session_scope() as session:
        service = OnboardingService(session)
        resumed_reply = await service.start(
            FAKE_TELEGRAM_ID, "resilience", "R"
        )
        show("/start again, after 'restart'", resumed_reply)

    state_after, cv_count_after = await get_state_and_cv_count(user_id)
    print(
        f"\nAfter restart: onboarding_state={state_after.value!r} "
        f"cv_count={cv_count_after}"
    )

    reasked_for_cv = "send me your cv" in resumed_reply.text.lower()
    reasked_for_roles = "what roles are you targeting" in resumed_reply.text.lower()

    if state_after is not OnboardingState.AWAITING_ROLES or reasked_for_cv:
        print(
            "\nBUG: the user was not resumed at AWAITING_ROLES after the "
            "simulated restart. Onboarding state was not durable."
        )
        sys.exit(1)

    if cv_count_after != cv_count_before:
        print(
            "\nBUG: the stored CV was lost across the simulated restart "
            f"(cv_count went from {cv_count_before} to {cv_count_after})."
        )
        sys.exit(1)

    if not reasked_for_roles:
        print(
            "\nBUG: resumed reply did not re-ask the AWAITING_ROLES "
            "question. Wording may have drifted from this check."
        )
        sys.exit(1)

    print(
        "\nOK: after disposing and re-initialising the engine, /start "
        "resumed at AWAITING_ROLES and the stored CV was not lost. "
        "onboarding_state is durable across a process restart."
    )

    if not keep:
        async with session_scope() as session:
            cvs = await session.scalars(select(CV).where(CV.user_id == user_id))
            stored_paths = [cv.storage_path for cv in cvs if cv.storage_path]

            await session.execute(
                delete(User).where(User.telegram_id == FAKE_TELEGRAM_ID)
            )

        intake = CVIntakeService()
        for path in stored_paths:
            intake.delete(user_id, path)

        print("\nTest user and stored CV removed. Pass --keep to leave them.")

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
