import os
from collections.abc import Mapping
from functools import lru_cache

from pydantic import model_validator
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

    # Seconds to wait between embedding requests.
    #
    # The live quota is per-MINUTE, not per-day:
    # global_embed_content_requests_per_minute_per_base_model. A run of
    # 99 rows fires 13 batches back to back and gets a 429 partway
    # through; observed behaviour was nine calls succeeding and the
    # tenth failing, so the ceiling is near ten requests per minute.
    #
    # 7.0 rather than 6.0 deliberately. Ten requests per minute means
    # 6.0 seconds apart is EXACTLY the limit, and a threshold hit
    # exactly at its boundary is the case that fails while looking
    # like it should pass. 7.0 gives roughly 8.5 requests per minute,
    # which is under it rather than on it.
    #
    # Costs about 90 seconds for the full 99 rows. That is cheaper
    # than a 429 halfway through, which costs a second run.
    embedding_seconds_between_calls: float = 7.0

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

    # --- Matching & scoring (Day 8) -----------------------------------------
    #
    # The five weights come from the plan spreadsheet's "Matching &
    # Scoring" tab, which is authoritative. They sum to exactly 1.0
    # in binary floating point as written -- verified, not assumed --
    # but the validator below stays because these are meant to be
    # re-tuned and an edited set will not be so lucky.
    weight_skill: float = 0.30
    weight_semantic: float = 0.20
    weight_experience: float = 0.20
    weight_location: float = 0.15
    weight_title: float = 0.15

    # Bumped by hand whenever any weight above changes.
    #
    # Exists from the first run rather than being added later. A
    # stored score whose weights are unknown cannot be compared with
    # a score computed today, and recommendations is a stored table
    # -- so retro-fitting this would leave every early row with
    # unknowable provenance. Re-tuning the weights is written into
    # the Recommendation docstring as a goal, which makes this a
    # certainty rather than a possibility.
    weights_version: int = 1

    # Raw cosine similarity is rescaled onto 0-1 by an affine map
    # against these two FIXED anchors, not by min-max within the
    # day's candidate set.
    #
    # Candidate-set rescaling was rejected for a reason that is not
    # the obvious one. The obvious objection is that the best match
    # always scores 1.0 even when it is terrible. The stronger
    # objection is that recommendations is a STORED table: a score
    # computed relative to that day's neighbours can never be
    # reproduced, because it was never a property of the pair. It
    # was a property of the batch. That is the same defect that made
    # JobMatch frozen.
    #
    # Why rescaling is needed at all: two English documents in the
    # same domain have a high similarity floor. Measured over one
    # AI/ML CV against all 99 stored jobs, raw similarity ran from
    # 0.5058 to 0.6928 -- a spread of 0.187, with 49 of 98 jobs
    # between 0.59 and 0.65. Fed straight into a 20% weight that is
    # a spread of 3.74 points out of 20, so a 20% signal would
    # behave like roughly 4%. The ordering was correct; only the
    # range was useless.
    #
    # With these anchors that same data spans 0.58 to 19.28 points
    # out of 20.
    semantic_anchor_low: float = 0.50
    semantic_anchor_high: float = 0.70

    # A raw similarity of exactly semantic_anchor_high maps to 1.0
    # WITHOUT being clamped -- (0.70-0.50)/(0.70-0.50) is 1.0 by
    # arithmetic. So the clamp counters in scoring_runs must count
    # `raw > high` and `raw < low`, strictly, never >= or <=.
    # Counting the exact boundary as a clamp would report a loss of
    # discrimination that did not happen. Same rule as
    # fit_to_budget(), which truncates on `len(text) > max_chars`
    # because a document of exactly max_chars fits.
    #
    # anchor_high sits 0.0072 above the highest similarity ever
    # observed, so nothing clamps today. It is kept tight on purpose:
    # widening it to buy headroom would compress every real score
    # now, to protect against a case that has never occurred. The
    # clamp counters are what will say when that changes.

    # Applied to RAW similarity, never to the rescaled score. An
    # absolute floor is what stops a day on which only irrelevant
    # jobs were ingested from producing a confident-looking top
    # match. Of the 99 stored jobs only 12 clear this, and those 12
    # include all three genuine ML matches.
    #
    # Compared with >=. A job at exactly 0.62 CLEARS the floor.
    # Day 6's `median < 500` check stayed silent when the median was
    # exactly 500; the boundary is the case that fails while looking
    # like it should pass, so it gets stated here and tested at
    # exactly this value rather than near it.
    semantic_notify_floor: float = 0.62

    # A signal with no data on one side ABSTAINS: its score is NULL
    # and its weight is removed from the denominator, rather than
    # scoring 0.0 and dragging the total down for a data gap. But a
    # score built from 35% of the weight is not comparable with one
    # built from 100%, so notification requires a minimum coverage.
    #
    # This also protects user_preferences.notification_threshold,
    # which defaults to 0.7. On a renormalised score, 0.7 is easy to
    # reach with two signals and hard with five -- without a floor,
    # that threshold quietly means something different for every job.
    #
    # Compared with >=. Coverage of exactly 0.55 qualifies.
    min_weight_covered_to_notify: float = 0.55

    # Experience taper. A job wanting [lo, hi] years scores a
    # candidate holding x years as:
    #
    #   lo <= x <= hi          -> 1.0
    #   x > hi                 -> 1.0   overqualified is not a
    #                                   mismatch; that is the user's
    #                                   call to make, not ours
    #   x < lo                 -> max(0.0, 1.0 - (lo - x) / TAPER)
    #
    # A taper rather than a cliff because "two years wanted, one and
    # a half held" and "two years wanted, none held" must not be the
    # same number. At TAPER = 3.0 those are 0.83 and 0.33.
    #
    # x == lo and x == hi both score exactly 1.0. Tests are written
    # at exactly lo and exactly hi, not at lo - 0.1.
    #
    # A NULL hi with lo present ("5+ years") is treated as infinity,
    # not as an abstain -- an open-ended range is information.
    experience_taper_years: float = 3.0

    # Quality penalties. Applied as a MULTIPLIER on the weighted
    # total, not as a sixth weighted signal.
    #
    # A signal answers "does this person fit this job". These answer
    # "is this posting trustworthy", which is a different axis --
    # a staffing agency's listing is not a worse fit, it is a less
    # reliable description of one. Folding it into the weights makes
    # it impossible to explain to a user why their score moved.
    #
    # Multiplier rather than subtraction so the result can never go
    # negative, and stored separately from the weighted total so
    # both can be read on their own.
    #
    # Penalties, NOT filters. Four companies account for 29 of the
    # 99 active jobs; filtering them removes nearly a third of the
    # corpus, and some agency postings are real. A filtered job is
    # not ranked low, it is absent -- and absent is the failure mode
    # this project keeps paying for.
    quality_multiplier_agency: float = 0.90
    quality_multiplier_no_city: float = 0.95

    # Comma-separated, matched case-insensitively against a
    # normalized company string, by EXACT equality -- not substring.
    #
    # Substring matching would fire "meta" against "Metadata
    # Solutions Pvt Ltd" and apply a penalty to the wrong job with
    # nothing anywhere reporting it. Exact matching instead misses
    # variants like "Vrinda International Pvt Ltd" -- but that miss
    # shows up as a lower quality_penalty_agency count in
    # scoring_runs, where someone can see it. A loud miss beats a
    # silent false positive.
    #
    # Seeded from the live table on Day 8:
    #   Vrinda International  11    Weekday AI  8
    #   TestHiring             6    JobCrexa    4
    #
    # TestHiring is included although it is not a staffing agency.
    # The test this list applies is not "is this a recruiter" but
    # "is this the employer that has the work", and a name that is
    # plainly test or aggregator data fails it the same way.
    #
    # In settings rather than in Python because a hardcoded list
    # inside scoring logic is a data table wearing a code costume,
    # and it goes stale the first time ingestion finds a fifth one.
    staffing_agency_companies: str = (
        "vrinda international,weekday ai,jobcrexa,testhiring"
    )

    # Title tokens that appear in almost every posting and therefore
    # carry no matching signal. Scoring runs on what is left.
    #
    # If BOTH sides reduce to nothing after these are removed, the
    # title signal ABSTAINS rather than scoring 0.0. "Senior
    # Engineer" against "Lead Developer" tells us nothing in either
    # direction, and a 0.0 there would punish a job for having a
    # generic title.
    title_weak_tokens: str = (
        "engineer,developer,senior,junior,lead,principal,staff,"
        "associate,manager,executive,specialist,consultant,analyst,"
        "sr,jr,i,ii,iii,intern,trainee,officer,head,chief"
    )

    # --- Job enrichment (Day 8, Part 2) -------------------------------------
    # Seconds between generation calls during the enrichment pass.
    #
    # Day 7 found the embedding quota is per MINUTE, not per day, at
    # roughly ten requests. The generation quota for gemini_model is
    # a DIFFERENT quota and its ceiling is unknown. 7.0 is carried
    # over on the assumption that the shape is the same, for the same
    # reason 7.0 was chosen there: at ten per minute, 6.0 seconds is
    # EXACTLY the limit, and a threshold sitting on its boundary is
    # the one that fails while looking like it should pass.
    #
    # Cost at 99 jobs: roughly 26 minutes end to end. That number is
    # printed by scripts/enrich_jobs.py --dry-run so that a slow run
    # is not mistaken for a hung one -- Day 5 lost three hours to a
    # Gemini call that hung rather than failing.
    enrichment_seconds_between_calls: float = 7.0

    # Per-call timeout for enrichment. 90 seconds, from measurement.
    #
    # Twelve timed calls across four isolate runs spanned 7.4s to
    # over 45s, and the request is NOT what drives the spread: the
    # smallest call in the set -- a four-word prompt with no schema --
    # was consistently among the SLOWEST at 29.2, 37.9, 37.8 and
    # 38.5 seconds, while a longer call with the full schema returned
    # in 7.4s. A 45s ceiling sat inside that range and produced one
    # timeout on a request that had already succeeded twice with
    # identical text.
    #
    # That matters more here than in a diagnostic. On this path a
    # timeout increments skills_extraction_attempts, which removes
    # the row from list_needing_extraction()'s default filter -- so a
    # job that was merely slow stops being retried, permanently and
    # silently.
    #
    # 90 is twice the largest value observed. Same shape of reasoning
    # as the 7.0 second pacing over 6.0: a threshold sitting on its
    # observed boundary is the one that fails while looking like it
    # should pass.
    enrichment_timeout_seconds: float = 90.0

    # How many times a job may be attempted before it is left alone.
    #
    # NOT the binary `attempts == 0` filter the embedding pass uses.
    # That was right there because those failures were deterministic:
    # a dimension mismatch or an empty document fails identically
    # every time, so one attempt is all the information there is.
    #
    # Here the failure is VARIANCE. Fifteen timed calls across five
    # isolate runs ran from 7.4s to 74.1s for requests that did not
    # change, against a 90s ceiling. A row that times out at 91s is
    # not broken, it is slow -- and a binary filter would remove it
    # from every future run permanently, for being unlucky once.
    #
    # Three attempts gives a slow row three chances and still stops a
    # genuinely broken one. skills_extraction_error records which
    # kind each failure was, so a row stuck at 3 can be read as
    # "timed out three times" or "rejected three times" rather than
    # just "failed".
    enrichment_max_attempts: int = 3

    # Comma-separated. Entries a model returns as "skills" that are
    # personal qualities rather than named competencies.
    #
    # This is not tidiness. Skill score is
    # |job skills AND candidate skills| / |job skills|, so anything
    # in that denominator which no CV will ever list silently lowers
    # every good candidate's 30% signal. A live call returned
    # "Strong communication skills" alongside seven real
    # technologies: eight in the denominator instead of seven, and a
    # candidate holding five of them scores 0.625 instead of 0.714,
    # for a reason nothing anywhere reports.
    #
    # Matched as a SUBSTRING of the normalized skill, unlike the
    # agency list which is matched by exact equality. Different
    # reasoning: a company name is a fixed string, while these arrive
    # wrapped in wording the model chose -- "strong communication
    # skills", "excellent communication". The false-positive risk is
    # real and accepted, and the count of what was dropped is
    # recorded so it can be inspected.
    enrichment_soft_skill_terms: str = (
        "communication,teamwork,team player,leadership,interpersonal,"
        "problem solving,problem-solving,collaboration,collaborative,"
        "time management,attention to detail,work ethic,adaptability,"
        "self-motivated,self motivated,fast learner,detail oriented,"
        "detail-oriented,multitasking,proactive"
    )

    # Whole-word triggers for work_mode inference. Deterministic and
    # in our code, not asked of the model -- the same reasoning as
    # compute_total_experience_years(): a rule can be re-run,
    # unit-tested, and explained to a user who asks why.
    enrichment_remote_terms: str = "remote,work from home,wfh"
    enrichment_hybrid_terms: str = "hybrid"

    # There is deliberately NO enrichment batch size.
    #
    # Day 7 batched embeddings eight at a time and the obvious move
    # is to do the same here: 13 calls instead of 99. It is the wrong
    # move, and the reason is what gemini-embedding-2 did -- it
    # returned ONE vector for a batch of eight inputs, and code
    # zipping that back positionally would have attached one
    # embedding to the first row and silently lost seven.
    #
    # That failure was CATCHABLE, because a returned count can be
    # compared against an input count. The generation equivalent is
    # not. A model returning six objects for eight descriptions, or
    # eight objects in a different order, produces output that parses
    # cleanly and stores job 3's skills against job 5. No dimension
    # check, no length assert, nothing to notice it -- and the damage
    # shows up only as rankings that feel slightly wrong.
    #
    # One job, one call. The job_id travels in the prompt and comes
    # back in the response, and it is compared against the row's own
    # id before anything is written.

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @model_validator(mode="after")
    def _check_weights_sum_to_one(self) -> "Settings":
        """Fail at import if the five scoring weights do not sum to 1.

        An explicit raise rather than `assert`, because assert
        statements are stripped under `python -O` -- and a check that
        disappears in one run mode is worse than no check, since it
        teaches you to trust something that is not always there.

        The comparison is `> EPSILON`, so a total off by exactly
        EPSILON passes. The value is far below any weight anyone
        would type by hand; it exists to absorb binary floating point
        representation, not to permit sloppy weights.

        Why this is fatal rather than a warning: with weights summing
        to, say, 0.95, every score in the system is quietly 5% low.
        Nothing is out of range, nothing looks broken, and the
        ranking is even still correct -- only the absolute numbers
        are wrong, which is exactly what the notification threshold
        reads.
        """
        EPSILON = 1e-9

        total = (
            self.weight_skill
            + self.weight_semantic
            + self.weight_experience
            + self.weight_location
            + self.weight_title
        )

        if abs(total - 1.0) > EPSILON:
            raise ValueError(
                "Day 8 scoring weights must sum to 1.0; "
                f"they sum to {total!r}. Adjust the weight_* settings "
                "and bump weights_version."
            )

        return self

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

    @property
    def staffing_agency_list(self) -> frozenset[str]:
        """Agency names as a lookup set of normalized keys.

        A frozenset rather than a list: this is consulted once per
        scored pair, and membership on a list is a scan. At 99 jobs
        that is irrelevant and at 99,000 it is not.

        Stored as a comma-separated string rather than list[str] for
        the same reason as adzuna_keyword_list -- pydantic-settings
        parses a list-typed field from the environment as JSON, so
        an ordinary comma-separated value raises a parse error at
        import time instead of doing the obvious thing.

        Unlike adzuna_keyword_list, an EMPTY value here means no
        penalty rather than no filter. That is the safe direction:
        an empty list scores every posting at full quality, which is
        wrong in a way scoring_runs.quality_penalty_agency reports
        as 0 on a corpus known to contain 29.
        """
        parsed = [item.strip().lower() for item in
                  self.staffing_agency_companies.split(",")]
        return frozenset(item for item in parsed if item)

    @property
    def title_weak_token_set(self) -> frozenset[str]:
        """Weak title tokens as a lookup set. See staffing_agency_list."""
        parsed = [item.strip().lower() for item in
                  self.title_weak_tokens.split(",")]
        return frozenset(item for item in parsed if item)

    @property
    def soft_skill_term_list(self) -> tuple[str, ...]:
        """Soft-skill substrings, lowercased. See staffing_agency_list.

        A tuple rather than a frozenset because these are matched by
        substring in a loop, not by membership, so hashing buys
        nothing and a stable order makes the dropped-term reason
        reproducible.
        """
        parsed = [t.strip().lower() for t in
                  self.enrichment_soft_skill_terms.split(",")]
        return tuple(t for t in parsed if t)

    @property
    def remote_term_list(self) -> tuple[str, ...]:
        parsed = [t.strip().lower() for t in
                  self.enrichment_remote_terms.split(",")]
        return tuple(t for t in parsed if t)

    @property
    def hybrid_term_list(self) -> tuple[str, ...]:
        parsed = [t.strip().lower() for t in
                  self.enrichment_hybrid_terms.split(",")]
        return tuple(t for t in parsed if t)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


# --- LangSmith telemetry (Day 10) ----------------------------------------
#
# langsmith arrives as a langchain-core dependency and activates from the
# PROCESS ENVIRONMENT ALONE. Nothing in this repository imports it, sets
# these variables, or declares a Settings field for them -- and none of
# that is evidence about the machine the graph runs on. Once Day 10 runs
# the graph unattended, an enabled tracer ships graph state to a third
# party, and graph state is CV-derived profile text and job descriptions.
#
# Both spellings are checked. langchain-core renamed LANGCHAIN_* to
# LANGSMITH_* and still honours the old names, so checking only the newer
# pair would return a clean answer while tracing was on.
_TRACING_ENV_VARS = (
    "LANGCHAIN_TRACING_V2",
    "LANGSMITH_TRACING",
    "LANGCHAIN_API_KEY",
    "LANGSMITH_API_KEY",
    "LANGCHAIN_ENDPOINT",
    "LANGSMITH_ENDPOINT",
    "LANGCHAIN_PROJECT",
    "LANGSMITH_PROJECT",
)


def tracing_vars_set(environ: Mapping[str, str]) -> list[str]:
    """Which tracing variables are set in `environ`. NAMES ONLY.

    Never returns, logs or formats a VALUE. Two of the names above are
    API keys, and CLAUDE.md section 3 records nine leak incidents, none
    of which came from printing `.env` -- every one came from something
    handling a secret incidentally while doing another job. A diagnostic
    that echoed what it found would be the tenth.

    Takes the environment as an argument rather than reading os.environ
    itself, so it can be driven from a dict without touching the
    process. Output is sorted, so the message a failure prints is the
    same on every machine.

    An empty or whitespace-only value counts as UNSET, because that is
    how langchain-core reads them. Note the consequence, which is
    deliberate: any other value counts as set, so LANGCHAIN_TRACING_V2
    set to "false" is reported. That is stricter than langchain-core and
    it fails closed -- somebody disabling tracing by value rather than
    by unsetting gets an error they can act on, which is the direction
    to be wrong in when the alternative is silently exporting CV text.
    """
    return sorted(name for name in _TRACING_ENV_VARS if (environ.get(name) or "").strip())


def assert_tracing_disabled(environ: Mapping[str, str] | None = None) -> None:
    """Refuse to build a graph while a tracer could be listening.

    Raises rather than warning, and that is the entire point. A warning
    about telemetry is read after the run that already sent the data;
    there is no such thing as retracting it. Nothing downstream of this
    is worth the export.

    The message names the variables and never their values.
    """
    found = tracing_vars_set(os.environ if environ is None else environ)
    if found:
        raise RuntimeError(
            "LangSmith tracing appears to be enabled: "
            + ", ".join(found)
            + ". The workflow refuses to run while a tracer could receive "
            "CV-derived profile text and job descriptions. Unset these "
            "variables in the environment. (Names only are shown here; "
            "two of them are credentials.)"
        )
