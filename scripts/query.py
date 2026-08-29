"""Run a raw SQL query against the configured database and print the results.

Uses the same DATABASE_URL as the application (see app.core.config).

    python -m scripts.query "SELECT * FROM users"
"""

import asyncio
import sys

if sys.platform == "win32":
    # psycopg's async driver needs a selector event loop; Windows defaults
    # to the proactor loop, which it can't use.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import text

from app.db.session import dispose_engine, init_engine


async def run(sql: str) -> None:
    engine = init_engine()
    if engine is None:
        print("DATABASE_URL is not configured. Set it in your .env file.")
        return

    try:
        async with engine.connect() as connection:
            result = await connection.execute(text(sql))

            if not result.returns_rows:
                await connection.commit()
                print(f"OK. {result.rowcount} row(s) affected.")
                return

            columns = list(result.keys())
            rows = result.fetchall()

            print(" | ".join(columns))
            print("-" * 70)
            for row in rows:
                print(" | ".join(str(value) for value in row))

            print(f"\n({len(rows)} row(s))")
    finally:
        await dispose_engine()


def main() -> None:
    if len(sys.argv) < 2:
        print('Usage: python -m scripts.query "SELECT * FROM users"')
        sys.exit(1)

    sql = sys.argv[1]
    asyncio.run(run(sql))


if __name__ == "__main__":
    main()
