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

    # gemini-3.6-flash: confirmed by isolation, not by the docs page or
    # by memory. gemini-3.7-flash does not serve for this project's API
    # key, but it does not 404 — the request hangs until the client
    # timeout, so a model the account cannot serve is indistinguishable
    # from a network fault unless it's tested with nothing else in the
    # request. A six-word prompt with no schema hung for 45s on
    # 3.7-flash and returned 'pong' in 5.9s on 3.6-flash; the full
    # extraction schema returned valid CVProfile JSON on 3.6-flash in
    # 9.3s. gemini-2.5-flash, tried earlier, is retired outright — the
    # API's own 404 for it names gemini-3.6-flash as the replacement.
    # scripts/gemini_isolate.py is the diagnostic that settles this
    # class of problem; run it against any new model before trusting it.
    gemini_model: str = "gemini-3.6-flash"

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
