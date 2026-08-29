from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AI Job Hunting Agent"
    app_env: str = "development"
    debug: bool = True

    telegram_bot_token: str = ""
    telegram_mode: str = "polling"

    database_url: str = ""

    log_level: str = "INFO"

    # --- CV upload (Day 3) -------------------------------------------------
    # Where downloaded CV files land. Relative paths resolve against the
    # process working directory, which is the repository root in dev.
    cv_storage_dir: str = "storage/cvs"

    # Telegram's Bot API refuses to serve downloads above 20 MB, so this
    # is a policy limit well inside a hard one. A CV above 5 MB is
    # almost always a scan, which Day 4's text extraction cannot read
    # anyway.
    max_cv_size_mb: int = 5

    # --- CV extraction (Day 4) ----------------------------------------------
    gemini_api_key: str = ""

    # gemini-3.7-flash is Google's current recommended general-purpose
    # model (ai.google.dev/gemini-api/docs/models, checked 2026-08-29),
    # not a value trusted from memory — model IDs change too often and
    # too fast for that to be safe.
    gemini_model: str = "gemini-3.7-flash"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def max_cv_size_bytes(self) -> int:
        return self.max_cv_size_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
