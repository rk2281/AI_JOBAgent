import asyncio
import sys
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

from app.core.config import settings
from app.db.base import Base
import app.db.models  # noqa: F401  -- registers all 11 models on Base.metadata

# Windows defaults to ProactorEventLoop, which psycopg's async mode cannot use.
# This must run before any asyncio.run() call in this file.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Alembic's Config object, giving access to the values inside alembic.ini
config = context.config

# Inject the database URL from our own settings instead of hardcoding it in
# alembic.ini. alembic.ini is committed to Git; the password must not be.
# The %% escaping is required because configparser treats a lone % as a format
# character, and this password contains %40.
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The schema we WANT. Alembic compares this against what the database HAS.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Print the SQL instead of running it. Useful for review, not used often."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """The actual migration run. Synchronous, by Alembic's design."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine and hand a connection to the sync migration code."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()