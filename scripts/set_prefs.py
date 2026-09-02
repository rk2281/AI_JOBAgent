"""One-off: widen user 2's target roles and preferred locations.

Written as a file rather than passed to scripts.query, because JSON
literals do not survive PowerShell quoting -- two attempts were
mangled before this existed.
"""

from __future__ import annotations

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import text

from app.db.session import dispose_engine, init_engine

ROLES = ["machine learning engineer", "ai engineer", "data scientist", "python developer"]
CITIES = ["noida", "gurgaon", "delhi", "bangalore"]


async def run() -> None:
    engine = init_engine()
    if engine is None:
        print("DATABASE_URL is not configured.")
        return

    async with engine.begin() as connection:
        before = await connection.execute(
            text("SELECT target_roles, preferred_locations FROM user_preferences WHERE user_id = 2")
        )
        print("before:", before.fetchall())

        await connection.execute(
            text(
                "UPDATE user_preferences SET target_roles = CAST(:r AS jsonb), "
                "preferred_locations = CAST(:c AS jsonb) WHERE user_id = 2"
            ),
            {"r": __import__("json").dumps(ROLES), "c": __import__("json").dumps(CITIES)},
        )

        after = await connection.execute(
            text("SELECT target_roles, preferred_locations FROM user_preferences WHERE user_id = 2")
        )
        print("after: ", after.fetchall())

    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(run())