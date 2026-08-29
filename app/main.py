from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.bot.application import create_bot_application
from app.core.config import settings
from app.core.logging import setup_logging
from app.db.session import (
    check_database_connection,
    dispose_engine,
    init_engine,
)


telegram_application = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global telegram_application

    setup_logging()

    init_engine()

    if settings.telegram_mode == "polling":
        if settings.telegram_bot_token:
            telegram_application = create_bot_application()

            await telegram_application.initialize()
            await telegram_application.start()

            if telegram_application.updater is not None:
                await telegram_application.updater.start_polling()

    yield

    if telegram_application is not None:
        if telegram_application.updater is not None:
            await telegram_application.updater.stop()

        await telegram_application.stop()
        await telegram_application.shutdown()

    await dispose_engine()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/")
async def root():
    return {
        "message": "AI Job Hunting Agent is running",
        "docs": "/docs",
        "health": "/health",
        "readiness": "/health/ready",
    }


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": settings.app_name,
    }


@app.get("/health/ready")
async def readiness():
    telegram_configured = bool(settings.telegram_bot_token)

    database_configured = bool(settings.database_url)
    database_connected = await check_database_connection()

    all_ready = telegram_configured and database_connected

    return {
        "status": "ready" if all_ready else "degraded",
        "dependencies": {
            "telegram": {
                "configured": telegram_configured,
            },
            "database": {
                "configured": database_configured,
                "connected": database_connected,
            },
        },
    }