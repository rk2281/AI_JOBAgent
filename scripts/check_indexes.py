"""Confirm the two HNSW indexes still exist in PostgreSQL.

scripts/show_schema.py cannot answer this. It reads Base.metadata,
and these two indexes are absent from Base.metadata by design — they
were created with raw op.execute() in migration 563b5bb86690 because
HNSW is not expressible through the ORM's Index(). That absence is
exactly why Alembic autogenerate has twice proposed dropping them, and
why every migration needs this check run afterwards.

So this queries pg_indexes, the catalog view of what the database
actually has, rather than what the models believe.

    python -m scripts.check_indexes
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

from app.db.session import dispose_engine, init_engine

EXPECTED = ("ix_cv_versions_embedding_hnsw", "ix_jobs_embedding_hnsw")


async def main() -> int:
    engine = init_engine()
    if engine is None:
        print("No DATABASE_URL configured.")
        return 1

    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
            )
            present = {row[0] for row in result}
    finally:
        await dispose_engine()

    missing = [name for name in EXPECTED if name not in present]

    for name in EXPECTED:
        print(f"{'OK   ' if name in present else 'GONE '} {name}")

    if missing:
        print(f"\n{len(missing)} index(es) missing. A migration dropped them.")
        return 1

    print("\nBoth HNSW indexes present.")
    return 0


if __name__ == "__main__":
    # psycopg's async driver deadlocks under the default Windows
    # ProactorEventLoop. Every standalone script in this project sets
    # this; the FastAPI app does not need it because uvicorn already
    # selects a compatible policy.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    raise SystemExit(asyncio.run(main()))
