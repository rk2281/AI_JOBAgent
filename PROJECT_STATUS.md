# PROJECT_STATUS.md

Analysis only — no code changed. Produced by reading every file under
`app/`, `scripts/`, `alembic/`, and the test/doc scaffolding directly
(not summarized from `CLAUDE.md`, though `CLAUDE.md` and
`docs/MATCHING_AND_SCORING.md` were cross-checked against the code and
are cited where they add context the code itself doesn't state).

---

## 1. PURPOSE

This is a Telegram bot that finds jobs for one candidate and messages
them when a good match appears. A user onboards via Telegram: uploads
a CV (PDF/DOCX), the bot extracts a structured profile from it via
Gemini, and the user states target roles, locations, remote
preference, experience, and an alert threshold. Separately, a
scheduled pipeline (driven by a LangGraph workflow, run nightly via
Windows Task Scheduler) ingests job postings from the Adzuna API,
embeds both jobs and CVs into a shared vector space (pgvector) via
Gemini's embedding model, and extracts structured skills/experience
bounds from job postings via a second Gemini call. A scoring pass then
matches every candidate against every job on five weighted signals
(skill overlap, semantic similarity, experience fit, location,
title), and a three-gate notification rule decides which matches are
good enough to message the user about over Telegram, with feedback
buttons (Interested / Save / Not Relevant) recorded for future
(not-yet-implemented) ranking improvements.

---

## 2. STRUCTURE

Directory tree (`.venv`, `__pycache__`, `.git`, `.pytest_cache`
omitted):

```
app/
  api/                        EMPTY — dead scaffolding (see HEALTH)
    routes/                   EMPTY
  bot/                        Telegram adapter layer
    application.py            builds the python-telegram-bot Application
    commands.py                single source of truth for the /command list
    rendering.py               BotReply -> Telegram InlineKeyboardMarkup, and
                                the "which button was tapped" echo helper
    handlers/
      __init__.py               register_handlers(): wires commands, three
                                 prefixed CallbackQueryHandlers (onb:/fb:/pref:)
                                 plus an unconditional catch-all, in fixed order
      common.py                 /help, /ping, unknown_callback, global error_handler
      onboarding.py              /start /restart /status, document/photo/text
                                 handlers, inline-button callback
      preferences.py             /preferences command + its callback
      profile.py                 /profile (read-only), /update_cv
      feedback.py                 the Interested/Save/Not-Relevant button callback
  core/
    config.py                  pydantic-settings Settings (every tunable and
                                every threshold lives here); LangSmith
                                tracing-disabled guard
    logging.py                  silences httpx/httpcore INFO logging at import
                                time (Adzuna/Telegram credential-leak guard)
  db/
    base.py                     declarative Base + TimestampMixin
    session.py                  engine/session lifecycle; session_scope()
    models/                     11 files — see DATA section
    repositories/               8 files — see DATA section
  integrations/                the ONLY layer allowed to import a vendor SDK
    adzuna.py                   Adzuna job-search HTTP client
    gemini.py                   CV-extraction Gemini client (interactions API)
    gemini_embeddings.py         embedding Gemini client (jobs + CVs, shared)
    gemini_enrichment.py         per-job skill/experience-bounds Gemini client
    http_errors.py               turns an httpx exception into a credential-safe string
    telegram.py                  outbound notifier; token-redaction and safe
                                 error classification
  schedulers/                  EMPTY — dead scaffolding (see HEALTH)
  schemas/
    job.py                      RawJobPosting — the provider-agnostic job shape
    cv_profile.py                 CVProfile / ExperienceEntry / EducationEntry —
                                 the Gemini response schema
  services/                    business logic; no Telegram or vendor SDK imports
    job_ingestion.py            Adzuna fetch -> validate/dedupe/insert -> retire
    job_embedding.py             embed active jobs missing a vector
    job_enrichment.py            per-job skill + experience-bound extraction
    job_enrichment_rules.py      pure rules: skill filtering, work-mode inference
    job_scoring.py               the scoring driver: funnel counters, gate,
                                 status selection, run_scoring()
    scoring.py                   combine(): weight+renormalise+quality-multiply
                                 five signals into one ScoredPair
    scoring_signals.py            the five signal functions, each pure
    job_search.py                 pgvector nearest-neighbour search (no scoring)
    embedding_text.py             build_job_document / build_cv_document / fit_to_budget
    experience.py                 compute_total_experience_years() — union-of-
                                 intervals arithmetic, never asked of the model
    locations.py                  normalize_location() — shared by ingestion dedupe
                                 and location matching
    cv_intake.py                  validate + store an uploaded CV file
    cv_text.py                    PDF/DOCX -> raw text, NUL-byte stripping
    cv_extraction.py              extract_cv(): 3-phase transaction around the
                                 Gemini call, writes profiles + cv_versions
    cv_embedding.py               embed the active CV version (RETRIEVAL_QUERY)
    onboarding.py                 the 7-step Telegram onboarding state machine
    preferences.py                 /preferences: edit one field post-onboarding
    profile.py                    /profile, /update_cv service logic
    profile_view.py                pure render_profile(snapshot) -> BotReply
    message_routing.py             decides whether free text answers onboarding
                                 or a pending /preferences edit
    notification_delivery.py       select_notifiable() (the gate) +
                                 deliver_notifications() (no gate) + run_*()
    notification_message.py        format_job_notification() — pure BotReply builder
    feedback.py                    records Interested/Save/Not-Relevant taps
    replies.py                     framework-free BotReply/Button dataclasses
  utils/                        EMPTY — dead scaffolding (see HEALTH)
  workflows/                    the ONLY package that imports langgraph
    graph.py                     8 nodes, 3 conditional edges, tracing guard first
    nodes.py                     one function per node; each self-skips w/ reason
    routing.py                    3 pure router functions, no DB/LangGraph import
    state.py                     AgentState TypedDict, normalisers,
                                 build_run_summary() (pure)
  main.py                       FastAPI app; lifespan starts the polling bot;
                                 /health, /health/ready
alembic/
  env.py                        injects DATABASE_URL from Settings; async migration runner
  versions/                     11 migration files — see DATA section
docs/                          15 design-decision / session-record markdown files
prompts/                       5 staged-prompt files (agent instructions used on
                                past days; intentionally left unfolded — CLAUDE.md §7)
scripts/                       32 entry-point / diagnostic scripts — see ENTRY POINTS
  pack.ps1 / verify_archive.ps1 / schedule_agent.ps1 / run_nightly.ps1 /
  run_weekly_ingestion.ps1      PowerShell operational tooling
storage/cvs/                   uploaded CV files on disk, one subdir per user id
logs/                          nightly run logs (gitignored)
tests/                         46 unit test files + tests/integration/ (7 files)
run.py                          Windows-safe uvicorn entry point (see HEALTH-adjacent
                                note under CONFIG: ProactorEventLoop workaround)
```

---

## 3. DATA

**Database:** PostgreSQL with the `pgvector` extension. Accessed via
SQLAlchemy 2.0 async ORM over `postgresql+psycopg://` (async psycopg
driver — every standalone script sets
`WindowsSelectorEventLoopPolicy` first because this driver cannot run
on Windows' default `ProactorEventLoop`).

**Migrations:** `alembic/versions/`, run via `alembic/env.py`, which
pulls `DATABASE_URL` out of `Settings` rather than `alembic.ini`
(`alembic/env.py:27`). Chain, oldest to newest (11 files; the alembic
head is whichever has no migration pointing at it as `down_revision`):

```
7255dfea3285  initial_schema
  -> 563b5bb86690  add_pgvector_extension_and_embedding_*
  -> a41c9e2b7f30  add_onboarding_state_to_users
  -> c5d09f00d1e6  add_cv_extraction_status_fields
  -> 4b481a8ea241  add_superseded_at_to_cvs
  -> d7a3f1c92b40  add_job_ingestion_tables
  -> f2c81b3ea774  add_embedding_bookkeeping
  -> 9a4e7c1d5b82  add_day8_scoring_tables
  -> b3f7c21d9e40  add_agent_runs
  -> c8e2a15f4b93  add_day11_notification_delivery
  -> 4ca67e97fc0e  add_pending_preference_field   <- current head
```

Note: `CLAUDE.md` §6's "Alembic head" row still says `c8e2a15f4b93`
(10 migrations). The repo on disk has an 11th migration
(`4ca67e97fc0e`) on top of it. That table in `CLAUDE.md` is stale
documentation, not a code problem — flagged here so nobody trusts the
count without checking.

**Tables**, with columns and types as declared in `app/db/models/`:

- **`users`** (`user.py`) — `id` PK, `telegram_id` BigInteger unique
  indexed, `username`/`full_name` String, `is_active` Bool,
  `onboarding_state` String(32) (plain-string state machine, not a
  native enum — deliberate per every model's docstring, to avoid
  orphaned PG enum types on downgrade), `pending_preference_field`
  String(32) nullable, `created_at`/`updated_at`. Partial index
  `ix_users_onboarding_state_pending` on `onboarding_state <>
'complete'`.
- **`user_preferences`** — `id` PK, `user_id` FK unique,
  `target_roles`/`preferred_locations` JSONB list, `min_/max_
experience_years` Integer nullable, `remote_only` Bool,
  `notification_threshold` Float default `0.7`.
- **`profiles`** — `id` PK, `user_id` FK unique, `summary` Text
  nullable, `total_experience_years` Float nullable, `current_title`/
  `location` String, `skills` JSONB list (normalized catalog keys —
  the matching surface), `experience`/`education` JSONB list-of-dict,
  `active_cv_version_id` FK -> `cv_versions.id` (`SET NULL`).
- **`cvs`** (`cv.py`) — `id` PK, `user_id` FK, `file_name`/`file_type`/
  `file_size_bytes`, `telegram_file_id`, `storage_path`, `raw_text`
  Text nullable, `extraction_status` String(32) (`pending` /
  `extracting` / `complete` / `failed` / `no_text_layer` / `empty`;
  `superseded` is return-only, never stored), `extraction_error`,
  `extracted_at`, `superseded_at` (non-NULL means an older CV row
  the user has replaced).
- **`cv_versions`** — `id` PK, `cv_id` FK, `version` Integer (unique
  with `cv_id`), `extracted_profile` JSONB, `extraction_model`,
  `embedding` `vector(768)` nullable, `embedding_model`,
  `embedded_at`, `embedding_attempts` Integer default 0,
  `embedding_error`, `embedding_source_hash`. HNSW index
  `ix_cv_versions_embedding_hnsw` (created by raw SQL in migration
  `563b5bb86690`, redeclared in `__table_args__` so
  `alembic check` doesn't propose dropping it).
- **`jobs`** (`job.py`) — `id` PK, `source`/`external_id` (unique
  together as `uq_job_source_external`), `title`, `company`,
  `location`, `description` Text, `url`, `is_remote` Bool
  (hardcoded `False` at ingestion — Adzuna has no remote field),
  `min_/max_experience_years` Integer nullable, `posted_at`
  timestamptz nullable, `content_hash` String(64) unique
  (`uq_job_content_hash` — a _constraint_, not the plain index the
  column's own `index=True` used to imply; the model comment at
  `job.py:82-89` explicitly documents this drift from Day 6),
  `last_seen_at`, `is_active` Bool, five embedding-bookkeeping
  columns identical in shape to `cv_versions`', five
  skills-extraction-bookkeeping columns (`skills_extracted_at`,
  `skills_extraction_model`, `skills_extraction_attempts`,
  `skills_extraction_error`, `skills_source_hash`), `work_mode`
  String(16) nullable (`remote`/`hybrid`/`None`, never `onsite`),
  `is_excluded` Bool + `exclusion_reason`. HNSW index
  `ix_jobs_embedding_hnsw`, same redeclaration pattern as above.
- **`job_skills`** — composite PK (`job_id`, `skill_id`), both FKs
  cascade-delete, `is_required` Bool.
- **`skills`** (`skill.py`) — `id` PK, `name`, `normalized_name`
  String(128) unique indexed, `category` nullable.
- **`ingestion_runs`** (`ingestion.py`) — `id` PK, `source`, `status`
  (`running`/`complete`/`no_results`/`all_rejected`/`source_error`/
  `quota_exceeded`), `started_at`/`finished_at`, and a strict funnel:
  `queries_attempted`, `pages_fetched`, `records_fetched`,
  `normalize_failed`, `validation_failed`, `filtered_out`,
  `duplicates`, `inserted`, `retired`, `error_message`.
- **`ingestion_rejects`** — `id` PK, `run_id` FK cascade, `source`,
  `external_id` nullable, `stage` (`normalize`/`validate`), `reason`
  Text, `raw_payload` JSONB, `created_at`.
- **`embedding_runs`** (`embedding.py`) — `id` PK, `scope` String(32)
  indexed (`jobs` / `cv_versions` / **`job_skills`** — the enrichment
  pass reuses this exact table under a third scope value rather than
  getting its own table), `status` (8-member enum, see SCORING
  section), `model`, `started_at`/`finished_at`,
  `candidates_considered`, `skipped_empty_text`, `attempted`,
  `succeeded`, `failed`, `api_calls`, `remaining_null` (measured
  _after_ the pass, not derived from the funnel), `error_message`.
- **`scoring_runs`** (`scoring.py`) — `id` PK, `status` (8-member
  enum), `weights_version`, `started_at`/`finished_at`, a two-part
  funnel (`users_considered`/`users_skipped_no_cv`/`users_scored`;
  `jobs_considered`/`jobs_skipped_no_embedding`/
  `jobs_excluded_manual`/`jobs_scored`), `pairs_scored`, five
  `abstain_*` counters, `semantic_clamped_low`/`_high`,
  `semantic_raw_min`/`max`/`median`, `quality_penalty_agency`/
  `_no_city`, `jobs_remote`/`jobs_hybrid`, `score_min`/`max`/`median`,
  `distinct_score_count`, `notify_eligible`, `error_message`. **Does
  NOT have columns for the three-way `users_skipped_no_cv` breakdown**
  (`no_profile`/`no_active_cv`/`cv_not_embedded`) — those are
  persisted only on `agent_runs` (deliberate; see `job_scoring.py:249-263`).
- **`recommendations`** (`recommendation.py`) — `id` PK, `user_id` FK
  indexed, `job_id` FK indexed, unique together
  (`uq_recommendation_user_job`, so one _current_ row per pair, not a
  history), five signal-score `Float` columns **nullable = abstain**,
  `final_score` Float not-null indexed, `rank` Integer nullable,
  `semantic_raw` Float nullable (feeds the notification floor;
  distinct from the rescaled `semantic_score`), `weight_covered` Float
  not-null default `0.0`, `quality_multiplier` Float not-null default
  `1.0`, `weights_version`, `inputs_fingerprint` String(64) nullable
  (SHA-256 over the scoring inputs), `scoring_run_id` FK `SET NULL`,
  `match_reasons` JSONB list.
- **`notifications`** (`recommendation.py`) — an _attempt_ table, not
  a delivery table. `id` PK, `user_id` FK, `job_id` FK,
  `recommendation_id` FK `SET NULL`, `status` — a **native PostgreSQL
  ENUM** (`Enum(NotificationStatus, name="notification_status")`,
  unlike every other status column in the schema, which are all plain
  `String`). SQLAlchemy persists this enum by **member NAME**, so the
  PostgreSQL labels are `PENDING`/`SENT`/`FAILED` — uppercase — while
  `NotificationStatus.SENT.value` is `"sent"`. `sent_at`,
  `error_message` (only ever from `describe_telegram_error()`, never
  `str(exc)`), `trigger_source` String(32) (`scheduled`/
  `manual_test`). Partial unique index
  `uq_notification_sent_user_job` on `(user_id, job_id)` **`WHERE
status = 'SENT'`** (uppercase, matching the enum's real label) — at
  most one _successful_ delivery per pair; unlimited `pending`/
  `failed` rows.
- **`user_feedback`** — `id` PK, `user_id` FK, `job_id` FK,
  `recommendation_id` FK `SET NULL`, `action` — also a **native
  PostgreSQL enum** (`feedback_action`: `interested`/`not_relevant`/
  `saved`). Unique together on **three** columns
  (`user_id`,`job_id`,`action`), so the same action twice collapses
  (`ON CONFLICT DO NOTHING`) but different actions on the same job
  both persist — contradictory feedback is kept, not overwritten.
- **`agent_runs`** (`agent.py`) — one row per LangGraph run. `id` PK,
  `started_at` not-null (written _before_ the graph runs),
  `finished_at` nullable (an unfinished run is `finished_at IS
NULL`, not a status value), and ~35 further columns that are **all
  nullable**, one column per key of `build_run_summary()` — status,
  terminal_reason, notify_branch/eligible, the four `stages_*` JSONB
  lists + `errors` JSONB, per-stage ingestion/embedding/enrichment/
  scoring/notification outcomes, and the full skip-reason breakdown.
  Partial index `ix_agent_runs_unfinished` on `started_at WHERE
finished_at IS NULL`. No foreign keys to `ingestion_runs`/
  `embedding_runs`/`scoring_runs` — deliberately, so this audit trail
  survives those tables being cleaned up.

**Relationships:** `users` 1—1 `profiles`, 1—1 `user_preferences`,
1—N `cvs` (cascade-delete), 1—N `recommendations`, 1—N
`notifications`, 1—N `user_feedback`. `cvs` 1—N `cv_versions`
(cascade-delete). `profiles.active_cv_version_id` → `cv_versions.id`
(the specific version a profile was built from, not necessarily the
newest). `jobs` 1—N `job_skills` N—1 `skills`. `recommendations` N—1
`scoring_runs` (`SET NULL`). `notifications`/`user_feedback` N—1
`recommendations` (`SET NULL`).

---

## 4. PIPELINE

End-to-end trace, ingestion → Telegram notification, in call order.
Scheduled path (`scripts/schedule_agent.ps1` registers two Windows
Scheduled Tasks that both invoke `scripts/run_agent.py`, one nightly
with `--skip-ingestion`, one weekly with `--skip-enrichment` — see
`CLAUDE.md` §10 for why cadence is split):

1. **`scripts/run_agent.py:run()`** builds `initial_state()`
   (`app/workflows/state.py:140`), opens an `agent_runs` row
   (`AgentRunRepository.start()`, `app/db/repositories/agent.py:92`),
   then `build_graph().ainvoke(state)`
   (`app/workflows/graph.py:113`).
2. **`resolve_targets`** node (`app/workflows/nodes.py:76`) →
   `resolve_scoring_targets()` (`app/services/job_scoring.py:396`) —
   counts scorable users without doing any work; routes to `finalise`
   if nobody qualifies (`app/workflows/routing.py:35`).
3. **`discover_jobs`** node (`nodes.py:98`) → opens `AdzunaClient`
   (`app/integrations/adzuna.py:106`) → `run_ingestion()`
   (`app/services/job_ingestion.py:554`) →
   `JobIngestionService.run()`: fetches pages, validates, dedupes by
   `(source, external_id)` then by `content_hash`, inserts via
   `JobRepository.create()` (`app/db/repositories/job.py:66`),
   retires stale jobs (`_retire_stale_jobs`, `job_ingestion.py:478`),
   writes an `ingestion_runs` row.
4. **`embed_jobs`** node (`nodes.py:137`) → `run_job_embedding()`
   (`app/services/job_embedding.py:169`) →
   `GeminiEmbeddingClient.embed_documents()`
   (`app/integrations/gemini_embeddings.py:220`, task type
   `RETRIEVAL_DOCUMENT`) → `JobRepository.set_embedding()` writes
   `jobs.embedding`.
5. **`enrich_jobs`** node (`nodes.py:175`) → `run_enrichment()`
   (`app/services/job_enrichment.py:166`) →
   `GeminiEnrichmentClient.enrich_job()`
   (`app/integrations/gemini_enrichment.py:204`, one call per job,
   never batched) → `filter_and_normalize_skills()` +
   `infer_work_mode()` (`app/services/job_enrichment_rules.py`) →
   `JobRepository.replace_job_skills()` writes `job_skills`; sets
   `min_/max_experience_years`, `work_mode` on `jobs`.
6. **`score_and_rank`** node (`nodes.py:212`) → `run_scoring()`
   (`app/services/job_scoring.py:453`): for each scorable user,
   `JobRepository.nearest_to()` (pgvector `<=>` cosine distance,
   `app/db/repositories/job.py:287`) → the five signal functions in
   `app/services/scoring_signals.py` → `combine()`
   (`app/services/scoring.py:101`) → `rank()`
   (`scoring.py:210`) → `RecommendationRepository.upsert()`
   (`app/db/repositories/scoring.py:86`) writes `recommendations`;
   writes a `scoring_runs` row.
7. **`decide_notification`** node (`nodes.py:240`) reads
   `scoring.notify_eligible` (computed inside `run_scoring`'s loop
   via `is_notify_eligible()`) and calls `route_notification()`
   (`app/workflows/routing.py:75`) → `"notify"` or `"no_qualifying"`.
8. **`notify`** node (`nodes.py:263`, entered only when eligible ≥
   1. → `run_notification_delivery()`
      (`app/services/notification_delivery.py:621`) =
      `select_notifiable()` (re-applies the gate, cross-checked against
      `run_scoring`'s own evaluation — see §5) then
      `deliver_notifications()`: for each candidate,
      `format_job_notification()`
      (`app/services/notification_message.py:172`) builds a `BotReply`,
      `NotificationRepository.open_attempt()`
      (`app/db/repositories/notification.py:89`) commits a `pending`
      row _before_ the network call, `TelegramNotifier.send()`
      (`app/integrations/telegram.py:270`) calls python-telegram-bot's
      `Bot.send_message`, then `mark_sent()`/`mark_failed()`.
9. **`finalise`** node (`nodes.py:321`) stamps `finished_at`;
   `build_run_summary()` (`app/workflows/state.py:361`) is computed
   and written onto the already-open `agent_runs` row via
   `AgentRunRepository.finish()`.

**User-facing side**, independent of the nightly run: a Telegram
update arrives → `app/bot/handlers/__init__.py:register_handlers()`
dispatches by command name / callback-data prefix
(`^onb:`/`^fb:`/`^pref:`, catch-all last) → a service
(`OnboardingService`, `ProfileService`, `PreferencesService`,
`FeedbackService`) → a repository. A CV upload during onboarding
schedules a background task, `_extract_and_notify`
(`app/bot/handlers/onboarding.py:138`), which calls `extract_cv()`
(`app/services/cv_extraction.py:98`) →
`GeminiClient.extract_profile()` (`app/integrations/gemini.py:185`)
→ writes `profiles` + `cv_versions`. That CV version only becomes
scorable once a **separate** pass, `run_cv_embedding()`
(`app/services/cv_embedding.py:51`), embeds it — this is not wired
into the onboarding flow or the nightly graph; it must be run by
hand (`python -m scripts.embed_cvs`) or scheduled separately (see
BUGS/HEALTH — it is not part of `scripts/run_agent.py`'s node set).

**Feedback loop:** notification buttons → `app/bot/handlers/feedback.py`
→ `FeedbackService.handle_callback()` → `FeedbackRepository.record()`
writes `user_feedback`. Nothing reads this table back into ranking —
confirmed by grep and by `docs/MATCHING_AND_SCORING.md`'s own "Known
limits" section ("No learning from feedback yet ... Deliberate for
the MVP").

---

## 5. SCORING & GATING

### The match score

Five signals, each a `SignalScore(value: float | None, reason: str)`
from `app/services/scoring_signals.py`. **`None` means abstain**
("we could not look"); `0.0` means "we looked, no match" — this
distinction is the entire design of the model and is enforced by a
test keeping the five `recommendations` columns nullable
(`CLAUDE.md` §1; do not default an abstain to `0.0`).

| Signal     | Weight (config.py)                           | Function                                      | Abstains when                                                                      |
| ---------- | -------------------------------------------- | --------------------------------------------- | ---------------------------------------------------------------------------------- |
| Skill      | `weight_skill = 0.30` (`config.py:214`)      | `score_skill` (`scoring_signals.py:92`)       | job has no extracted skills, or profile has none                                   |
| Semantic   | `weight_semantic = 0.20` (`config.py:215`)   | `score_semantic` (`scoring_signals.py:48`)    | either side has no embedding                                                       |
| Experience | `weight_experience = 0.20` (`config.py:216`) | `score_experience` (`scoring_signals.py:115`) | candidate years unknown, or job states no range                                    |
| Location   | `weight_location = 0.15` (`config.py:217`)   | `score_location` (`scoring_signals.py:160`)   | no user location preference, or job location unresolvable/country-only             |
| Title      | `weight_title = 0.15` (`config.py:218`)      | `score_title` (`scoring_signals.py:224`)      | job title reduces to nothing after weak-token stripping, or so do all target roles |

Weights are validated to sum to exactly `1.0` at import time by
`Settings._check_weights_sum_to_one` (`config.py:575-612`), which
raises `ValueError` (not a warning) if not — `EPSILON = 1e-9`.
`weights_version = 1` (`config.py:229`), meant to be bumped by hand
whenever any weight changes; `recommendations.weights_version` and
`recommendations.inputs_fingerprint` exist so a re-tune is
detectable per-row.

**Formula**, `combine()` (`app/services/scoring.py:101-176`):

```
weight_covered  = sum(weight for signal in the 5 whose value is not None)
weighted_total  = 0.0                                     if weight_covered == 0.0
                = sum(weight * value for non-abstaining signals) / weight_covered   otherwise
final_score     = weighted_total * quality_multiplier
```

**Semantic rescaling** (`score_semantic`, `scoring_signals.py:48-74`):
raw pgvector cosine similarity is affine-mapped onto `[0,1]` against
two _fixed_ anchors — `semantic_anchor_low = 0.50`,
`semantic_anchor_high = 0.70` (`config.py:255-256`) — then clamped,
never computed relative to the day's candidate set (so a stored score
is reproducible). `raw == anchor_high` maps to exactly `1.0` without
being counted as clamped (strict `>`/`<` comparisons in
`semantic_clamp_flags`, `scoring_signals.py:77-89`).

**Experience taper** (`score_experience`, `scoring_signals.py:115-157`),
`experience_taper_years = 3.0` (`config.py:339`):
`x` in `[lo,hi]` → `1.0`; `x > hi` → `1.0` (overqualified is not
penalized); `x < lo` → `max(0.0, 1.0 - (lo-x)/taper)`. A `None`
`job_max` with `job_min` present is treated as an open-ended range,
not an abstain.

**Quality multiplier** (`assess_quality`, `scoring.py:37-81`) —
applied _after_ the weighted total, not folded in as a sixth signal:
`quality_multiplier_agency = 0.90`, `quality_multiplier_no_city =
0.95` (`config.py:359-360`), multiplied together (never additive, so
the result can't go negative). Agency match is exact-equality against
`staffing_agency_companies` (`config.py:385-387`, comma-separated,
parsed to a `frozenset` via `settings.staffing_agency_list`).

**Title weak-token stripping** — `title_weak_tokens`
(`config.py:397-401`, e.g. "engineer", "senior", "lead"…) is stripped
from both sides before token-overlap comparison
(`_tokenize_title`, `scoring_signals.py:219-221`).

### Every threshold that can block a notification

Three gates, all inclusive (`>=`), all required —
`is_notify_eligible()` (`app/services/job_scoring.py:156-185`):

```python
final_score    >= notification_threshold          # per-user
semantic_raw   >= settings.semantic_notify_floor   # 0.62, config.py:284
weight_covered >= settings.min_weight_covered_to_notify  # 0.55, config.py:298
```

| Threshold                                               | Where DEFINED                                                                                                                                  | Where READ                                                                                                                                                                                                                                                                                                                                                                                                       | Source                                                                                        |
| ------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| `notification_threshold`                                | `UserPreference.notification_threshold` column default `0.7`, `app/db/models/user.py:197-201` — **no `Settings` field exists for this at all** | `job_scoring.py:581-585` (`run_scoring`'s per-user loop, real gate); `notification_delivery.py:264-269` (`evaluate_candidates`, cross-checked); `scripts/notify_reachability_probe.py:84-94` (diagnostic, cross-checked)                                                                                                                                                                                         | **Per-user, from the DB** — with a hardcoded `0.7` fallback for a user with no preference row |
| `semantic_notify_floor`                                 | `Settings.semantic_notify_floor = 0.62`, `config.py:284`                                                                                       | `job_scoring.py:183` (real gate, inside `is_notify_eligible`); `notification_delivery.py:299-302` (local re-evaluation for the manual-test gate report, cross-checked at `notification_delivery.py:349-361`); `notify_reachability_probe.py`, `asymmetry_isolate.py`, `scoring_isolate.py` (read-only diagnostics, all re-derive it for display and are explicitly self-checking against `is_notify_eligible()`) | **Global config/env** (`SEMANTIC_NOTIFY_FLOOR`)                                               |
| `min_weight_covered_to_notify`                          | `Settings.min_weight_covered_to_notify = 0.55`, `config.py:298`                                                                                | Same call sites as above                                                                                                                                                                                                                                                                                                                                                                                         | **Global config/env**                                                                         |
| Five scoring weights                                    | `Settings.weight_*`, `config.py:214-218`                                                                                                       | `scoring.py:136-140` (`combine()`) only                                                                                                                                                                                                                                                                                                                                                                          | **Global config/env**                                                                         |
| `semantic_anchor_low/high`                              | `config.py:255-256`                                                                                                                            | `scoring_signals.py:69-70, 87-88` only                                                                                                                                                                                                                                                                                                                                                                           | **Global config/env**                                                                         |
| `experience_taper_years`                                | `config.py:339`                                                                                                                                | `scoring_signals.py:155` only                                                                                                                                                                                                                                                                                                                                                                                    | **Global config/env**                                                                         |
| `quality_multiplier_agency/no_city`                     | `config.py:359-360`                                                                                                                            | `scoring.py:66,72` only                                                                                                                                                                                                                                                                                                                                                                                          | **Global config/env**                                                                         |
| `max_notifications_per_user` (delivery cap, not a gate) | `config.py:319`                                                                                                                                | `notification_delivery.py:135` (`_max_per_user()`)                                                                                                                                                                                                                                                                                                                                                               | **Global config/env**                                                                         |

**Flagged divergence — the same conceptual default expressed three
different ways.** `notification_threshold`'s fallback value (`0.7`)
is **not** a `Settings` field at all; it exists only as
`UserPreference`'s column default, and is separately hardcoded as the
literal `_DEFAULT_NOTIFICATION_THRESHOLD = 0.7` in three independent
places with no shared import between them:

- `app/services/job_scoring.py:89`
- `app/services/notification_delivery.py:123`
- `scripts/notify_reachability_probe.py:76`

Each site's own comment says this is deliberate ("Duplicated ...
rather than imported because that name is private") — it is a known,
accepted trade-off, not an oversight — but it means the default is
defined in three unlinked places plus the DB column default, and
nothing enforces they stay equal if the value is ever changed. See
BUGS #4.

**Delivery-side window, `_GATE_WINDOW = 25`**
(`notification_delivery.py:116`) — not a documented gate in
`docs/MATCHING_AND_SCORING.md`'s three-gate table, but it truncates
which recommendations are ever _considered_ for delivery before the
gate is applied at all. See BUGS #1 — this is the most consequential
finding in this report.

---

## 6. ENTRY POINTS

All under `scripts/`, run as `python -m scripts.<name>` (never
`python scripts/name.py` — `CLAUDE.md` §5). "Writes" means writes to
Postgres and/or calls a paid third-party API; "read-only" means
neither.

| Script                              | What it does                                                                                                                                                   | Key args                                                                                                                              | DB / API                                                                                           |
| ----------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| `run_agent.py`                      | Drives the full LangGraph workflow once; the production entry point                                                                                            | `--user-id`, `--dry-run`, `--skip-ingestion/-embedding/-enrichment`, `--keywords`, `--locations`, `--max-pages`, `--enrichment-limit` | **Writes** unless `--dry-run` (then scoring-only rehearsal, no ingestion/embedding even attempted) |
| `score_jobs.py`                     | Runs one scoring pass by hand; `--top/--bottom/--explain` read back stored rows                                                                                | `--user-id`, `--dry-run`, `--top`, `--bottom`, `--explain JOB_ID`                                                                     | **Writes** unless `--dry-run`                                                                      |
| `ingest_jobs.py`                    | Runs one Adzuna ingestion pass                                                                                                                                 | `--keywords`, `--locations`, `--max-pages`, `--dry-run`                                                                               | **Writes + live API** unless `--dry-run`                                                           |
| `embed_jobs.py`                     | Embeds jobs missing a vector                                                                                                                                   | `--limit`, `--retry-failed`, `--recheck`, `--dry-run`                                                                                 | **Writes + live API** unless `--dry-run`                                                           |
| `embed_cvs.py`                      | Embeds active CV versions missing a vector                                                                                                                     | `--limit`, `--retry-failed`, `--dry-run`                                                                                              | **Writes + live API** unless `--dry-run`                                                           |
| `enrich_jobs.py`                    | Extracts skills + experience bounds per job                                                                                                                    | `--limit`, `--retry-failed`, `--dry-run`                                                                                              | **Writes + live API** unless `--dry-run`                                                           |
| `extract_cv.py`                     | Runs CV extraction for one user by hand                                                                                                                        | `--user-id` (required)                                                                                                                | **Writes + live API**, always                                                                      |
| `send_test_notification.py`         | Sends a **real** Telegram message; not named "dryrun" and makes no such promise                                                                                | `--user-id` (required), `--send`, `--top`                                                                                             | Read-only unless `--send`; then **writes + live API**                                              |
| `notify_reachability_probe.py`      | Read-only counterfactual grid: how many pairs would pass the gate at different coverage floors                                                                 | `--user-id`                                                                                                                           | **Read-only**                                                                                      |
| `notification_constraints_check.py` | Proves the two Day-11 DB constraints fire, inside a rolled-back transaction                                                                                    | none                                                                                                                                  | Opens a transaction, always rolls back — **no net writes**                                         |
| `check_run_freshness.py`            | Complains if no `agent_runs` row started inside the window                                                                                                     | `--max-age-hours` (default 26)                                                                                                        | **Read-only**                                                                                      |
| `check_indexes.py`                  | Confirms the two HNSW indexes exist in `pg_indexes`                                                                                                            | none                                                                                                                                  | **Read-only**                                                                                      |
| `config_selftest.py`                | Prints scoring settings and proves the weights validator actually fires                                                                                        | none                                                                                                                                  | Neither (in-process only, no engine)                                                               |
| `e2e_verify.py`                     | Drives one fixture candidate through every real stage; TRUNCATES the target DB first                                                                           | `--database-url` (required, must contain "test"), `--with-telegram`                                                                   | **Writes** (and truncates), always                                                                 |
| `asymmetry_isolate.py`              | Read-only breakdown of the abstention-asymmetry effect on `notify_eligible`                                                                                    | `--run-id`                                                                                                                            | **Read-only**                                                                                      |
| `concurrent_claim_dryrun.py`        | Fires two concurrent CV extractions to prove the claim lock excludes a second task                                                                             | `--user-id` (required)                                                                                                                | **Writes + live API** — despite the name (see BUGS #5)                                             |
| `embedding_isolate.py`              | Six escalating Gemini embedding calls to characterize the provider (dimension, normalization, task_type, batching)                                             | `--stage`                                                                                                                             | Live API, no DB                                                                                    |
| `enrichment_isolate.py`             | Three escalating Gemini calls for the enrichment schema/hang diagnosis; `--job-id` mode reads a real stored job                                                | `--job-id`                                                                                                                            | Live API; DB only in `--job-id` mode (read)                                                        |
| `gemini_isolate.py`                 | Three escalating Gemini calls for the CV-extraction hang diagnosis                                                                                             | none                                                                                                                                  | Live API, no DB                                                                                    |
| `offline_extraction_dryrun.py`      | Verifies the post-Gemini extraction logic (3-phase transaction, skill normalization, concurrent claim) by replaying a stored profile instead of calling Gemini | `--user-id` (required)                                                                                                                | **Writes**, no live API                                                                            |
| `onboarding_dryrun.py`              | Drives the full onboarding happy path against the real DB, prints every bot message, no Telegram                                                               | `--keep`                                                                                                                              | **Writes** (fake user, deleted at end unless `--keep`)                                             |
| `onboarding_edgecases_dryrun.py`    | Exercises restart-after-complete and the three CV-rejection paths                                                                                              | `--keep`                                                                                                                              | **Writes** (fake user, deleted unless `--keep`)                                                    |
| `query.py`                          | Runs arbitrary raw SQL against the configured DB and prints results                                                                                            | positional SQL string                                                                                                                 | **Read or write**, whatever the SQL says — no sanitization, developer tool                         |
| `restart_resilience_dryrun.py`      | Proves onboarding survives a full engine dispose/reinit (closest available stand-in for a process restart)                                                     | `--keep`                                                                                                                              | **Writes** (fake user, deleted unless `--keep`)                                                    |
| `scorable_targets_check.py`         | Cross-checks `resolve_scoring_targets()` against an independent raw-SQL oracle                                                                                 | `--user-id`                                                                                                                           | **Read-only** (runs `run_scoring(dry_run=True)`)                                                   |
| `scoring_isolate.py`                | Separates the four causes of a wrong score (weights / embeddings / skills / code) for one job/user pair                                                        | `--job-id`, `--user-id` (required)                                                                                                    | **Read-only**                                                                                      |
| `search_jobs.py`                    | pgvector similarity search; `--self-check` proves a job's own vector returns itself at similarity 1.0                                                          | `--user-id` / `--text` / `--self-check` (mutually exclusive), `--limit`, `--ef-search`, `--explain`                                   | `--text` **calls the live embedding API**; other modes read-only                                   |
| `set_prefs.py`                      | One-off: hardcodes new `target_roles`/`preferred_locations` for `user_id = 2`                                                                                  | none                                                                                                                                  | **Writes**, hardcoded to user 2                                                                    |
| `show_schema.py`                    | Prints the `CREATE TABLE`/`CREATE INDEX` SQL SQLAlchemy would generate, from `Base.metadata` only                                                              | none                                                                                                                                  | Neither (no DB connection at all)                                                                  |
| `source_isolate.py`                 | Three escalating Adzuna calls to separate network/credentials/quota/query-match causes of an empty ingestion                                                   | none                                                                                                                                  | Live API, no DB                                                                                    |
| `verify_archive.py`                 | Scans an arbitrary zip for forbidden patterns (`.env`, `storage/cvs`, `logs/`, etc.) before it's shared                                                        | path to a zip, or `--self-test`                                                                                                       | **Read-only** (reads the zip, never modifies it)                                                   |

`.ps1` operational scripts: `pack.ps1` builds a safe `git archive`
(excludes `.env`/`storage/`/`.git/`); `verify_archive.ps1` wraps
`verify_archive.py`; `schedule_agent.ps1` registers the two Windows
Scheduled Tasks; `run_nightly.ps1` / `run_weekly_ingestion.ps1` are
the generated task bodies (both call `scripts.run_agent` with
different skip flags, log to `logs/`, and propagate `run_agent.py`'s
exit code as the task's own).

---

## 7. CONFIG

**Names only — no values, keys, or secrets below.**

All fields belong to `Settings` in `app/core/config.py` (env-file
`.env`, case-insensitive, `extra="ignore"`). Env var name = field
name uppercased.

```
APP_NAME  APP_ENV  DEBUG  LOG_LEVEL
TELEGRAM_BOT_TOKEN  TELEGRAM_MODE
DATABASE_URL
CV_STORAGE_DIR  MAX_CV_SIZE_MB
GEMINI_API_KEY  GEMINI_MODEL
ADZUNA_APP_ID  ADZUNA_APP_KEY  ADZUNA_COUNTRY
ADZUNA_RESULTS_PER_PAGE  ADZUNA_MAX_PAGES_PER_RUN  ADZUNA_MAX_DAYS_OLD
ADZUNA_QUERY_KEYWORDS  ADZUNA_QUERY_LOCATIONS  ADZUNA_SORT_BY
JOB_RETIRE_AFTER_DAYS  JOB_RETIRE_REQUIRES_RUN_WITHIN_DAYS
GEMINI_EMBEDDING_MODEL  EMBEDDING_DIMENSION  EMBEDDING_BATCH_SIZE
EMBEDDING_SECONDS_BETWEEN_CALLS  EMBEDDING_TASK_TYPE_DOCUMENT
EMBEDDING_TASK_TYPE_QUERY  EMBEDDING_MAX_CHARS  EMBED_AFTER_INGESTION
WEIGHT_SKILL  WEIGHT_SEMANTIC  WEIGHT_EXPERIENCE  WEIGHT_LOCATION  WEIGHT_TITLE
WEIGHTS_VERSION
SEMANTIC_ANCHOR_LOW  SEMANTIC_ANCHOR_HIGH  SEMANTIC_NOTIFY_FLOOR
MIN_WEIGHT_COVERED_TO_NOTIFY
MAX_NOTIFICATIONS_PER_USER
EXPERIENCE_TAPER_YEARS
QUALITY_MULTIPLIER_AGENCY  QUALITY_MULTIPLIER_NO_CITY
STAFFING_AGENCY_COMPANIES  TITLE_WEAK_TOKENS
ENRICHMENT_SECONDS_BETWEEN_CALLS  ENRICHMENT_TIMEOUT_SECONDS
ENRICHMENT_MAX_ATTEMPTS  ENRICHMENT_SOFT_SKILL_TERMS
ENRICHMENT_REMOTE_TERMS  ENRICHMENT_HYBRID_TERMS
```

Read directly from `os.environ` (not `Settings` fields at all —
deliberately, per `config.py`'s own docstring, because
`langchain-core` reads both spellings and nothing in this repo should
declare a field that would encourage setting them):

```
LANGCHAIN_TRACING  LANGCHAIN_TRACING_V2  LANGSMITH_TRACING
LANGCHAIN_API_KEY  LANGSMITH_API_KEY
LANGCHAIN_ENDPOINT  LANGSMITH_ENDPOINT
LANGCHAIN_PROJECT  LANGSMITH_PROJECT
```

`.env.example` documents a subset of the above (the secrets and the
operationally-important Adzuna/Gemini/Telegram/DB values) with
placeholder values; everything else falls back to its `Settings`
default. Two of `.env.example`'s example values are themselves
misleading — see BUGS #2 and #3.

---

## 8. HEALTH

- **Three directories are dead scaffolding**: `app/api/` (with an
  empty `routes/` subdirectory), `app/schedulers/`, `app/utils/` —
  confirmed via `ls`/`find`, zero `.py` files in any of them. Nothing
  in `app/` or `scripts/` imports from any of the three (grep found
  no references). Likely leftover from an earlier planned layout
  where scheduling and API routes had their own packages before the
  project settled on `app/workflows/` + FastAPI's single `main.py`.
- **No `TODO`/`FIXME`/`XXX` markers anywhere under `app/`** (grep
  returned zero matches). The project's convention is long prose
  comments explaining _why_, not inline TODO markers — consistent
  with what was actually found.
- **Six `except Exception:` blocks total, all in `app/`, all
  deliberately scoped and commented `# noqa: BLE001` with a stated
  reason**: `app/db/session.py:127` (the standard rollback-then-reraise
  in `session_scope()` — correct, not swallowed, re-raises after
  rollback); `app/services/notification_delivery.py:560,606,617`
  (best-effort bookkeeping writes on an already-failure path, logged
  via `logger.exception`, never silently dropped);
  `app/bot/handlers/onboarding.py:261` and
  `app/bot/handlers/preferences.py:82` (best-effort message-edit,
  the message may be too old to edit — logged at `debug`, not fatal);
  `app/bot/handlers/feedback.py:74` (a callback query must always be
  answered or the button spins forever — the exception is logged in
  full and a generic reply substituted). None of the six are bare
  `except:` (no exception type at all) and none swallow silently
  without a log line — this is a genuinely disciplined pattern, not
  a smell.
- **`app/services/cv_embedding.py:44` imports "private"
  underscore-prefixed symbols (`_Counters`, `_classify`) directly
  from `app/services/job_embedding.py`.** Deliberate (the module
  docstring says it "shares its shape but not its code"), and it
  works, but it means renaming or changing the signature of either
  symbol in `job_embedding.py` would silently break
  `cv_embedding.py` without any public-API contract or import-linter
  catching it — a maintenance trap for whoever next edits
  `job_embedding.py` without grepping for underscore-prefixed
  cross-module imports first.
- **The graph's `failed` terminal status is unreachable in the
  current code.** `select_graph_status()`
  (`app/workflows/state.py:310-358`) checks `if errors: return
STATUS_FAILED` first, but grepping `app/workflows/` for
  `state["errors"]` or a returned `"errors"` key across all 8 node
  functions in `nodes.py` turns up nothing — no node ever populates
  `state["errors"]`. This matches `CLAUDE.md`'s own §7 record
  ("Nothing writes `state["errors"]`... only `degraded` can actually
  occur") and is confirmed, not new, by this read of `nodes.py`.
- **The value `64` (pgvector `ef_search` floor) is duplicated as two
  independently-named constants** with the same value and the same
  underlying justification but no shared import:
  `_MIN_EF_SEARCH = 64` (`app/services/job_scoring.py:82`) and
  `DEFAULT_EF_SEARCH = 64` (`app/services/job_search.py:30`). Lower
  risk than the notification-threshold triplication (§5) because
  these two code paths serve genuinely different callers (scoring
  vs. ad-hoc search), but still two numbers that must be kept in
  step by a human noticing.
- **`docs/` and `prompts/` together hold 20 markdown files**,
  several explicitly self-described as unfolded/contradictory working
  notes (`CLAUDE.md` §7: "The Day 10 prompts are now committed,
  unfolded and self-contradicting on purpose"). Not a code-health
  issue, but worth knowing before trusting any single doc file over
  another — `CLAUDE.md` itself is the only one asserted current, and
  even it has at least the one stale migration-count row noted in
  §3 of this report.
- **CV embedding is not wired into any automated path.** The
  workflow graph's `embed_jobs` node embeds _jobs_; nothing in
  `app/workflows/` calls `run_cv_embedding()`
  (`app/services/cv_embedding.py:51`) — it exists only as
  `scripts/embed_cvs.py`, run by hand. A brand-new user who finishes
  onboarding is not scorable until somebody remembers to run that
  script (or schedules it separately). Not necessarily a "bug" —
  might be intentional given the low daily embedding-quota ceiling —
  but it is not documented as a decision anywhere in `CLAUDE.md`, and
  it is easy to read `scripts/run_agent.py`'s docstring
  ("`app/workflows/` is 8 nodes... runs persisted to `agent_runs`")
  and wrongly assume CV embedding is part of that nightly loop.

---

## 9. BUGS

Ordered by severity. The first is a new finding from this read; the
rest range from newly-observed-but-low-impact to already-documented
(and still present).

### 1. `_GATE_WINDOW = 25` can silently exclude eligible recommendations from delivery — `app/services/notification_delivery.py:107-116, 245-273, 313-371`

**What's wrong:** `evaluate_candidates()` (line 245) only ever looks
at a user's **top 25 recommendations by `final_score`**
(`top_with_jobs_for_user(user_id, limit=_GATE_WINDOW)`, line
271-273) before the three-gate test is applied at all. The comment
justifying `_GATE_WINDOW = 25` (lines 107-115) argues this is safe
because the window is ordered by `final_score` descending and
`_GATE_WINDOW >= max_notifications_per_user` (asserted at line
333-336): _"a row dropped by the window would have been dropped by
the send cap below it anyway."_

That reasoning only holds if eligibility were monotonic with
`final_score` — it is not. The other two gates,
`semantic_raw >= semantic_notify_floor` and `weight_covered >=
min_weight_covered_to_notify`, are independent quantities from
`final_score`. It is entirely possible for a user's top 25
recommendations (by score) to contain fewer than
`max_notifications_per_user` (3) rows that pass _all three_ gates —
say, because the top-scoring rows happen to have low
`weight_covered` — while row 30 in that user's ranked list, with a
lower `final_score` but a full-coverage, high-semantic profile,
would pass all three gates and _should_ be sent. That row is never
even fetched, let alone evaluated, because the window truncates the
query itself before the gate runs.

**Why it matters:** `run_scoring()`'s `notify_eligible` counter
(`job_scoring.py`, computed over the _entire_ scored set with no
window) can therefore be strictly greater than what
`select_notifiable()`/`deliver_notifications()` ever finds and sends,
for a reason that isn't among the causes the codebase's own
comments list for that disagreement. `app/workflows/state.py:451-459`
explicitly documents that `notify_eligible` and
`notifications_eligible_selected` "can legitimately differ" and gives
two examples — "a job retired between the two, a user changed their
threshold" — **the window truncation is a third cause, and it is not
mentioned anywhere**, so a human reading a run's summary sees two
disagreeing numbers with an incomplete list of explanations for why.
The practical exposure depends on how many recommendations a user has
relative to 25 and how correlated `final_score` is with the other two
gate inputs on real data; at ~99 jobs/user today the risk is
probably low, but the table has no ceiling on job count and nothing
here scales the window with it.

**Fix direction (not applied — analysis only):** either widen the
query past `_GATE_WINDOW` when fewer than `cap` eligible rows are
found within it, or document the truncation as a fourth, deliberate
cause of `notify_eligible` vs. `notifications_eligible_selected`
disagreement — whichever is intended should be a decision, not an
implicit consequence of an efficiency comment that doesn't hold.

### 2. `.env.example`'s example `GEMINI_MODEL` value is the one this project's own code found unusable — `.env.example:70`, `app/core/config.py:39-51`

`.env.example:70` reads `GEMINI_MODEL=gemini-3.7-flash`. But
`app/core/config.py:39-50` documents, from direct measurement, that
`gemini-3.7-flash` "does not serve for this project's API key ... the
request hangs until the client timeout" — a six-word prompt hung for
45 seconds — and that `gemini-3.6-flash` is the model that actually
works, which is what `Settings.gemini_model` defaults to
(`config.py:51`). A developer who copies `.env.example` to `.env` and
fills in the placeholder Gemini key while leaving this line as
written (a very ordinary way to read an example file) would silently
reintroduce the exact multi-hour hang `docs/` records Day 4/5 losing
time to, because a hung request looks identical to a slow one until
the timeout finally fires.

### 3. `.env.example` documents `TELEGRAM_MODE=webhook` as a valid choice; the code hard-rejects it — `.env.example:37-39`, `app/core/config.py:526-559`

`.env.example:37-38` says `# polling or webhook. Polling needs no
public URL, which is why it is the default for local development.`
implying both are supported. `config.py`'s own validator,
`_check_telegram_mode_is_recognised` (lines 526-568), explicitly
special-cases `"webhook"` with its own `ValueError` message
(`"TELEGRAM_MODE=webhook is not implemented..."`, lines 552-559) —
it is the _one_ value singled out for a dedicated error rather than
falling into the generic "not recognised" branch, specifically
because (per the docstring at lines 540-542) it "reads like a
supported alternative and is not implemented anywhere." The
validator does its job — this fails loudly at process startup rather
than silently — so the practical damage is limited to wasted time
for whoever tries it, but the example file directly contradicts the
validator it's describing.

### 4. Default `notification_threshold` fallback (`0.7`) duplicated as three unlinked literals — `app/services/job_scoring.py:89`, `app/services/notification_delivery.py:123`, `scripts/notify_reachability_probe.py:76`

See §5 above for full detail. All three currently agree with
`UserPreference.notification_threshold`'s column default
(`app/db/models/user.py:199`), and each site's comment explains the
duplication is deliberate rather than accidental — this is a latent
risk, not a live defect. If the intended default is ever changed,
there is nothing (no test, no shared constant) that would catch only
two of the three being updated.

### 5. `scripts/concurrent_claim_dryrun.py`'s name promises no writes; it performs a real Gemini call and mutates state — `scripts/concurrent_claim_dryrun.py:1-16`

The filename and module docstring ("Prove the extraction claim
actually excludes a second task") give no indication this is a
mutating script, and the project's own convention (stated explicitly
elsewhere, e.g. `scripts/notification_constraints_check.py`'s
docstring: _"'dryrun' in a script name is a promise of no writes"_)
is violated by this file's own name. Line 16 of its docstring does
say "This makes one real Gemini call... and costs quota," but that
caveat is buried in prose, not signalled by the name the way
`send_test_notification.py` (which is _not_ named "dryrun" and warns
in capitals) does it correctly. `CLAUDE.md` §"Open after Day 10 Part
4" already flags this exact file as needing a rename to
`concurrent_claim_probe.py`; as of this read, that rename has **not**
been applied — the file is still named `concurrent_claim_dryrun.py`.
Low severity because it's a manually-invoked diagnostic script, not
on any automated path, but it remains a live landmine for anyone who
runs it expecting the "dryrun" name to mean what it means everywhere
else in this codebase.

---

_Everything in `CLAUDE.md` §1 ("Do not 'fix' these") and its Day
12/13 additions was cross-checked against the current code while
writing this report and is treated as settled, deliberate behavior —
none of it is repeated here as a bug._
