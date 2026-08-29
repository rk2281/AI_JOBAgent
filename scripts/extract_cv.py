"""Run CV extraction for one user by hand, no Telegram.

    python -m scripts.extract_cv --user-id 2

The second caller of app.services.cv_extraction.extract_cv, alongside
the background task fired from app.bot.handlers.onboarding after an
upload. Useful for retrying a FAILED extraction, or for running
extraction on a CV that predates this pipeline — every CV uploaded on
Day 3 has extraction_status='pending' from the migration default and
has never actually been processed.
"""

import argparse
import asyncio
import sys

if sys.platform == "win32":
    # psycopg's async driver needs a selector event loop; Windows defaults
    # to the proactor loop, which it can't use.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.db.session import dispose_engine, init_engine
from app.services.cv_extraction import extract_cv


async def run(user_id: int) -> None:
    engine = init_engine()
    if engine is None:
        print("DATABASE_URL is not configured. Set it in your .env file.")
        return

    try:
        result = await extract_cv(user_id)

        print(f"status={result.status.value}")
        if result.error:
            print(f"error={result.error}")
    finally:
        await dispose_engine()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--user-id",
        type=int,
        required=True,
        help="users.id to run extraction for (the internal ID, not the Telegram ID)",
    )
    arguments = parser.parse_args()

    asyncio.run(run(arguments.user_id))


if __name__ == "__main__":
    main()
