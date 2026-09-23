import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import text  # noqa: E402
from app.db.session import dispose_engine, init_engine, session_scope  # noqa: E402

async def main(path):
    with open(path, encoding="utf-8") as f:
        sql = f.read()
    init_engine()
    try:
        async with session_scope() as s:
            rows = (await s.execute(text(sql))).all()
            for r in rows:
                print(r)
    finally:
        await dispose_engine()

if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))