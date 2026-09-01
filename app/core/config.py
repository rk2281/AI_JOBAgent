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

    # --- Job ingestion (Day 6) ----------------------------------------------
    # Adzuna authenticates with a PAIR of values, not a single token,
    # and both travel as URL query parameters rather than headers.
    # That is why app/integrations/adzuna.py never logs a URL and why
    # every error path there goes through describe_http_error(): an
    # httpx exception's string form embeds the full request URL, so
    # printing one would leak both of these into the logs and into any
    # error column that stored it.
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""

    # Country code as it appears in the API path, e.g. /v1/api/jobs/in/.
    adzuna_country: str = "in"

    # 50 is the documented maximum per page. Fewer results per page for
    # the same number of jobs means more calls, and calls are the thing
    # that is rationed -- see adzuna_max_pages_per_run.
    adzuna_results_per_page: int = 50

    # The free tier is roughly 1,000 calls per MONTH, which is about 33
    # a day. Monthly rather than hourly matters: an hourly quota
    # recovers while you make coffee, a monthly one does not, and
    # spending it on Day 6 debugging leaves nothing for the Day 12
    # demo. This cap is deliberately low; raise it once ingestion is
    # known to work rather than before.
    adzuna_max_pages_per_run: int = 2

    # Freshness filter, sent to Adzuna as part of the request rather
    # than applied after the response arrives. A job the API never
    # sends is a call's worth of quota not spent and an embedding on
    # Day 7 not generated.
    adzuna_max_days_old: int = 14

    # Comma-separated. EMPTY IS THE DEFAULT AND IT MEANS NO FILTER --
    # every domain, tech and non-tech, across the whole country.
    #
    # Empty rather than a sensible-looking list of tech keywords on
    # purpose. A default list is an assumption about who this is for,
    # and a default that works is the hardest kind to notice and
    # remove later. Adzuna also ANDs every word in `what`, measured:
    # "machine learning engineer" returned 3 results where "machine
    # learning" returned 23 for the same city and window. So keyword
    # narrowing costs recall in a way that is invisible -- the jobs it
    # drops are never seen, so they cannot be missed. Narrowing that
    # cannot be audited does not belong in a default.
    #
    # Stored as a string rather than list[str] because pydantic-settings
    # parses a list-typed field from the environment as JSON, so
    # ADZUNA_QUERY_KEYWORDS=python,sales raises a parse error at import
    # time rather than doing the obvious thing. A string plus an
    # explicit split is less clever and does not surprise anyone.
    adzuna_query_keywords: str = ""
    adzuna_query_locations: str = ""

    # Adzuna sorts by relevance by default. With no keyword to be
    # relevant to, that ordering is arbitrary, and paginating an
    # arbitrary order means every run reads a different arbitrary
    # slice. Sorting by date makes page 1 mean "the newest postings",
    # which is both reproducible and the thing actually wanted.
    adzuna_sort_by: str = "date"

    # How long a job may go unseen before it is marked inactive.
    # Longer than adzuna_max_days_old on purpose: the freshness window
    # controls what is INGESTED, this controls what is RETIRED, and
    # retiring at the same age as the intake window would retire every
    # job the moment it aged out of the query that finds it.
    job_retire_after_days: int = 21

    # A safety interlock on retirement, not a tuning knob. See
    # JobIngestionService._retire_stale_jobs for why it exists.
    job_retire_requires_run_within_days: int = 3

    # --- Embeddings (Day 7) -------------------------------------------------
    # gemini-embedding-001, chosen by measurement rather than by
    # documentation. This key also serves gemini-embedding-2 and
    # gemini-embedding-2-preview, and -2 looked better on the one
    # number that does not matter (it returns a unit vector at 768
    # dimensions, which we get anyway by normalising) while failing
    # the two that do:
    #
    #   task_type: -2 ignores it. The same text embedded as
    #     RETRIEVAL_DOCUMENT and as RETRIEVAL_QUERY came back with
    #     cosine 1.000000 -- byte-identical. On -001 the same
    #     comparison gives 0.861247, so the parameter is real. That
    #     asymmetry is what lets a CV be compared against a job ad
    #     rather than only against other CVs.
    #
    #   batching: -2 returned ONE vector for a batch of eight inputs.
    #     Not an error -- one vector. Code that zipped that result back
    #     onto its rows positionally would attach a single embedding to
    #     the first job and silently lose the other seven. -001 returned
    #     eight vectors in input order, verified by embedding [X, Y, X]
    #     and confirming vector 0 and vector 2 are identical.
    #
    # Run `python -m scripts.embedding_isolate --model NAME` before
    # trusting any replacement. Changing this value means re-embedding
    # every stored row, which is why it is also written to
    # jobs.embedding_model and cv_versions.embedding_model.
    gemini_embedding_model: str = "gemini-embedding-001"

    # Must equal the dimension in the vector() columns created by
    # migration 563b5bb86690. Not a tuning knob: pgvector's HNSW index
    # does not support more than 2000 dimensions, so the model's native
    # 3072 output cannot be indexed at all and truncation is mandatory
    # rather than an optimisation. Changing this needs a migration, an
    # index rebuild AND a re-embed of every row.
    embedding_dimension: int = 768

    # Eight verified working end to end. The provider's true ceiling is
    # unknown and deliberately not probed -- a batch is all-or-nothing,
    # so a larger batch means a single bad row wastes more work. Raise
    # it only after `--stage F --batch-size N` confirms the larger size.
    embedding_batch_size: int = 8

    # Jobs are documents to be searched; a CV is the query searching
    # them. Passing the same task type for both would waste the one
    # thing -001 offers over -2.
    embedding_task_type_document: str = "RETRIEVAL_DOCUMENT"
    embedding_task_type_query: str = "RETRIEVAL_QUERY"

    # Our own limit, applied before the request is sent. The SDK's
    # EmbedContentConfig has an `auto_truncate` field whose default
    # behaviour is unknown, and provider-side truncation is the worst
    # kind of failure here: the call succeeds, a vector comes back, and
    # it describes half a CV. Truncating ourselves means we know it
    # happened and can count it.
    embedding_max_chars: int = 8000

    # Off by default. Day 6 left `embedding` nullable precisely so that
    # ingestion never blocks on an API call, and the two passes are
    # rationed by different quotas -- letting a Gemini failure abort a
    # run whose Adzuna calls are already spent trades a cheap problem
    # for an expensive one. Day 10 may turn this on once scheduling
    # exists.
    embed_after_ingestion: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def max_cv_size_bytes(self) -> int:
        return self.max_cv_size_mb * 1024 * 1024

    @property
    def adzuna_keyword_list(self) -> list[str]:
        """Parse ADZUNA_QUERY_KEYWORDS into a list.

        An empty setting yields [""] rather than [] -- a single empty
        keyword, which the client turns into a request with no `what`
        parameter at all. That is what makes "no configuration" mean
        "no filter" rather than "no queries", and it is the difference
        between a fresh install ingesting everything and a fresh
        install ingesting nothing.
        """
        parsed = [item.strip() for item in self.adzuna_query_keywords.split(",")]
        parsed = [item for item in parsed if item]
        return parsed or [""]

    @property
    def adzuna_location_list(self) -> list[str]:
        """Parse ADZUNA_QUERY_LOCATIONS into a list. See adzuna_keyword_list."""
        parsed = [item.strip() for item in self.adzuna_query_locations.split(",")]
        parsed = [item for item in parsed if item]
        return parsed or [""]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
