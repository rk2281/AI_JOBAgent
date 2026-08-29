# Codebase Guide

This document exists so that someone who has never opened this repository before can understand what every file does, why it exists, and what would break if it were removed. It was written after Day 3 (Telegram onboarding) landed on top of the Day 1 foundation (FastAPI service skeleton, database schema, Telegram polling). Nothing here should be treated as permanent architecture — several directories are placeholders for work that has not started yet, and that is called out explicitly below.

At a high level, this is a Telegram bot backed by a FastAPI process and a PostgreSQL database (hosted on Neon, with the pgvector extension enabled). A user talks to the bot, the bot walks them through a short onboarding conversation, and the answers are written to Postgres. FastAPI itself does almost nothing yet — its only real job right now is to own the process lifecycle (start the database engine, start the Telegram bot, shut both down cleanly) and expose a couple of health-check endpoints. The bot is the actual product surface today.

## Annotated directory tree

```
AI_JOB_HUNT_AGENT/
├── .env                          real secrets — never committed, see .gitignore
├── .env.example                  template for .env, placeholders only
├── .gitignore
├── alembic.ini                   Alembic config; no secrets, safe to commit
├── requirements.txt               pinned dependency versions
├── alembic/
│   ├── README                    boilerplate from `alembic init`
│   ├── env.py                    wires Alembic to Settings and to Base.metadata
│   ├── script.py.mako            template new migrations are generated from
│   └── versions/
│       ├── 7255dfea3285_initial_schema.py            creates all 11 tables
│       ├── 563b5bb86690_add_pgvector_extension_and_embedding_.py
│       └── a41c9e2b7f30_add_onboarding_state_to_users.py
├── app/
│   ├── main.py                   FastAPI app, lifespan, health endpoints
│   ├── core/
│   │   ├── config.py              Settings (reads .env)
│   │   └── logging.py             logging.basicConfig wrapper
│   ├── db/
│   │   ├── base.py                Declarative Base + TimestampMixin
│   │   ├── session.py             engine lifecycle, get_session, session_scope
│   │   ├── models/                one file per table group (SQLAlchemy ORM)
│   │   └── repositories/          only place that writes SQL against models
│   ├── services/                  business logic; no Telegram, no SQL
│   ├── bot/
│   │   ├── application.py         builds the python-telegram-bot Application
│   │   ├── rendering.py           BotReply → Telegram InlineKeyboardMarkup
│   │   └── handlers/               Telegram adapters; thin, no logic
│   ├── api/routes/                 EMPTY — placeholder, see below
│   ├── schedulers/                 EMPTY — placeholder, see below
│   ├── schemas/                    EMPTY — placeholder, see below
│   ├── utils/                      EMPTY — placeholder, see below
│   └── workflows/                  EMPTY — placeholder, see below
├── scripts/
│   ├── show_schema.py              prints the SQL the models generate
│   ├── query.py                    run arbitrary SQL against DATABASE_URL
│   └── onboarding_dryrun.py        drives the onboarding flow, no Telegram
├── storage/cvs/                    uploaded CV files land here (gitignored)
└── tests/
    ├── test_health.py              HTTP smoke tests
    ├── test_models.py              schema/metadata assertions, no DB needed
    └── test_onboarding.py          onboarding service + CV validation tests
```

## The layering rule

The codebase is built as four layers, and the dependency direction only ever points one way:

**handlers → services → repositories → models**

`app/bot/handlers/` is the Telegram adapter layer. A handler's job is to pull the relevant pieces out of a Telegram `Update`, open a database session, call exactly one method on a service, and turn the service's answer into a Telegram reply. A handler never writes SQL and never contains an `if` that depends on business state — `app/bot/handlers/onboarding.py` says as much directly in its module docstring: "If a handler in this file ever grows an `if`, that decision belongs in the service."

`app/services/` is where the actual rules live: what counts as a valid CV, what the next onboarding question is, how free text gets parsed into a list of roles. Services take a database session and call repositories; they never import anything from `app.bot`. That one-way rule is what lets `OnboardingService` be driven from `scripts/onboarding_dryrun.py` with no Telegram token and no network connection at all — the entire flow in Step 9 above ran through the service layer directly.

`app/db/repositories/` is the only code in the application allowed to write SQLAlchemy `select`/`insert` statements against the ORM models. `UserRepository` and `CVRepository` exist so that a service asks for "the user's latest CV" instead of composing a query itself. This is what `app/db/repositories/__init__.py` means when it says "Nothing calls SQLAlchemy directly from a Telegram handler — that is the rule this layer exists to enforce."

`app/db/models/` is the bottom layer: plain SQLAlchemy ORM classes and nothing else. They know about columns, relationships, and constraints, and nothing above them.

The reason nothing lower ever imports anything higher is that it is precisely what keeps the lower layers testable and reusable. `app/db/models/` has zero knowledge of Telegram or FastAPI, so it can be imported by Alembic in isolation. `app/services/` has zero knowledge of Telegram, so `OnboardingService` works identically whether it is called from `app/bot/handlers/onboarding.py` or from a future web endpoint under `app/api/routes/` — the same logic would not need to be rewritten. If a lower layer ever imported a higher one, that reusability would break immediately, and worse, it opens the door to circular imports (repositories importing services importing repositories).

## The two database entry points

`app/db/session.py` exposes exactly two ways to get a session, and they exist for two different execution contexts that this application straddles.

`get_session()` is a FastAPI dependency — an `async def` generator meant to be used with `Depends(get_session)` inside a route function. FastAPI opens the generator at the start of a request, hands the session to the route, and closes it when the request finishes. It is not used anywhere yet because `app/api/routes/` is empty, but it is what any future HTTP endpoint should use.

`session_scope()` is an async context manager for code that is not running inside a FastAPI request — which today means every Telegram handler. Its docstring explains the reasoning directly: Telegram handlers can't use `Depends()`, so they open a session with `async with session_scope() as session:` instead. The important behavioral difference is that `session_scope()` commits automatically when the `with` block exits normally and rolls back on any exception, keeping one Telegram update atomic — a handler that writes a CV row and then advances `onboarding_state` either does both or neither. Every handler in `app/bot/handlers/onboarding.py` follows the same shape: open `session_scope()`, call one `OnboardingService` method, let the block close before sending the Telegram reply.

The rule going forward is simple: FastAPI route code uses `get_session`; anything outside the request/response cycle (Telegram handlers today, a future scheduled job under `app/schedulers/` tomorrow) uses `session_scope`.

## How the onboarding state machine works

The onboarding flow is implemented in `app/services/onboarding.py` as a state machine, but deliberately not using python-telegram-bot's built-in `ConversationHandler`. The module's docstring explains why: `ConversationHandler` keeps conversation position in memory, so restarting the process strands every user who was mid-flow — the bot forgets what question it asked and the user has no way to resume. Persisting that same state to a second store (say, Redis) alongside the database would only create two records of the truth that can disagree with each other.

Instead, `users.onboarding_state` in the database is the single, only record of where a user is. It is a plain `VARCHAR(32)` column, not a native PostgreSQL enum — `app/db/models/user.py` explains that this was also a deliberate choice, because native enum types survive a migration `downgrade()` as orphaned types that have to be dropped by hand, which is exactly the class of problem the Day 2c work ran into. The allowed values are enforced in Python instead, via the `OnboardingState` enum in the same file (`NEW`, `AWAITING_CV`, `AWAITING_ROLES`, `AWAITING_LOCATIONS`, `AWAITING_REMOTE`, `AWAITING_EXPERIENCE`, `AWAITING_THRESHOLD`, `COMPLETE`).

Every entry point into `OnboardingService` — `start`, `handle_document`, `handle_text`, `handle_callback` — begins by reading the user's current `onboarding_state` from the database and ends by writing the new one, via `UserRepository.set_onboarding_state`. Because the state is re-read on every single update rather than trusted from memory, a user who taps a button from a week-old message (an inline keyboard that Telegram never expires) gets rejected: `handle_callback` compares the button's own step against the state actually stored for that user, and if they don't match it re-sends the current question instead of applying the stale answer. That is exactly the behavior the dry run in Step 9 exercised and confirmed.

The tradeoff this design accepts is that branching is written out by hand — `handle_text` and `handle_callback` are each an explicit `if state is X` chain — rather than declared through a framework. At seven steps, `_prompt_for()` keeps that readable: it is the single place holding the question text and buttons for every state, so resuming a flow and asking a question for the first time produce identical wording.

## The Alembic migration chain

Migrations apply in this order, each one's `down_revision` pointing at the one before it:

1. **`7255dfea3285` — initial schema.** Creates all 11 tables (`users`, `user_preferences`, `profiles`, `cvs`, `cv_versions`, `skills`, `jobs`, `job_skills`, `recommendations`, `notifications`, `user_feedback`) as they existed at the end of Day 1/2. Its `downgrade()` has a hand-added fix at the bottom: Alembic's autogenerate creates the two Postgres enum types (`notification_status`, `feedback_action`) implicitly while creating their tables, but never drops them again on downgrade, which would otherwise leave orphaned types that make a later re-upgrade fail with "type already exists." The two `sa.Enum(...).drop(...)` calls exist specifically to make the downgrade path clean.
2. **`563b5bb86690` — pgvector extension and embedding columns.** Runs `CREATE EXTENSION IF NOT EXISTS vector` before adding `Vector(768)` embedding columns to `cv_versions` and `jobs`, because Postgres won't recognize the column type otherwise. It also creates HNSW indexes for approximate nearest-neighbor search, using `vector_cosine_ops` — noted in the migration as needing to match the `<=>` operator used at query time, since a mismatched opclass makes Postgres silently ignore the index rather than error. Its `downgrade()` deliberately does not drop the `vector` extension itself, since `DROP EXTENSION` cascades to anything depending on it and leaving the extension installed costs nothing.
3. **`a41c9e2b7f30` — add onboarding_state to users.** Adds the column described above. Because it lands on a table that may already have rows, it uses `server_default='new'` so the `ALTER TABLE ... NOT NULL` doesn't fail against existing users — every pre-existing user is placed at `new`, which is correct, since they were never through the onboarding flow this column tracks. It also creates a partial index (`WHERE onboarding_state <> 'complete'`) so future queries like "who dropped out of onboarding" stay cheap without indexing users who no longer need to be found that way.

Current head is `a41c9e2b7f30`. The down-up-down-up cycle in Step 8 confirmed the whole chain reverses and reapplies cleanly with no orphaned state.

## File-by-file

### Root

`.env` holds real secrets — the Telegram bot token and the Neon `DATABASE_URL` — and is gitignored. `.env.example` mirrors its keys with placeholder values (`USER:PASSWORD@HOST.aws.neon.tech`, empty bot token) so a new developer knows what to fill in without ever seeing a real credential; it also documents the Day 3 CV settings (`CV_STORAGE_DIR`, `MAX_CV_SIZE_MB`) and a commented-out local Postgres fallback URL. `.gitignore` excludes the virtual environment, caches, `.env`, IDE folders, log files, the `storage/` directory (uploaded CVs, per-viewer content that has no business in Git), and `*.zip` archives. `alembic.ini` is Alembic's own config file; it deliberately has no database URL of its own (`sqlalchemy.url =` is left blank) because `alembic/env.py` injects the real one from `Settings` at runtime instead — this is what keeps the password out of a file that gets committed. `requirements.txt` pins every dependency version; removing or editing it without reinstalling risks the app running against different library versions than were tested.

### `alembic/`

`alembic/env.py` is the bridge between Alembic and the application. It pulls `settings.database_url` from `app.core.config` rather than reading `alembic.ini`, imports `app.db.models` so every table registers on `Base.metadata` before Alembic compares it against the live database, and — notably — sets `WindowsSelectorEventLoopPolicy()` on Windows before any `asyncio.run()` call, for the same reason `scripts/query.py` needed it: psycopg's async driver cannot use the Proactor event loop Windows defaults to. Removing this file breaks every `alembic` command outright. `alembic/script.py.mako` is the template new revisions are generated from when you run `alembic revision --autogenerate`; it is boilerplate and safe to leave alone. `alembic/versions/*.py` are the three migrations described above — each one is an immutable historical record once it has been applied anywhere, so the safe way to change the schema further is always a new migration file, never an edit to an old one.

### `app/main.py`

Defines the FastAPI app and its `lifespan` context manager, which is the only place that calls `init_engine()` and `create_bot_application()` — this is the actual startup sequence you saw in the uvicorn logs (`Database engine created` → bot `initialize()`/`start()` → polling starts). On shutdown it reverses the same sequence. It also defines the three HTTP routes that exist today: `/` (a static welcome payload), `/health` (always `ok` if the process is alive), and `/health/ready` (checks whether Telegram is configured and the database actually answers a query, via `check_database_connection()`). If this file were removed, there is no FastAPI app and no process entry point at all — `uvicorn app.main:app` would have nothing to import.

### `app/core/`

`config.py` defines `Settings`, a `pydantic-settings` class that reads `.env` and exposes typed config (`database_url`, `telegram_bot_token`, `cv_storage_dir`, `max_cv_size_mb`, etc.) through a single module-level `settings` instance, cached via `@lru_cache`. Every other module that needs configuration imports `settings` from here rather than reading the environment directly — removing it means every one of those imports breaks. `logging.py` is a four-line wrapper around `logging.basicConfig`, called once from `app.main`'s `lifespan`; removing it just means the app falls back to Python's unconfigured default logging (which still works, just without the timestamped format).

### `app/db/`

`base.py` defines `Base` (the SQLAlchemy `DeclarativeBase` every model inherits from) and `TimestampMixin` (the `created_at`/`updated_at` columns shared by nearly every table). Every model file imports from here; removing it breaks the entire ORM layer. `session.py` owns the engine and the two session entry points described above (`get_session`, `session_scope`) plus `init_engine`/`dispose_engine` (called once each from `app.main`'s lifespan) and `check_database_connection` (used by `/health/ready`). This is the only file that is allowed to call `create_async_engine` — its own docstring says so.

`db/models/` holds one file per logical table group: `user.py` (`User`, `UserPreference`, and the `OnboardingState` enum), `cv.py` (`CV`, `CVVersion`, including the pgvector `embedding` column), `job.py` (`Job`, `JobSkill`), `profile.py` (`Profile` — the structured, current-best-guess candidate profile derived from a CV), `recommendation.py` (`Recommendation`, `Notification`, `UserFeedback`, and their status/action enums), and `skill.py` (`Skill`, the normalized skills catalog). `models/__init__.py` re-exports all of them and its own docstring explains why that matters beyond convenience: importing this package is what guarantees every model registers on `Base.metadata`, which is what Alembic's autogenerate and `scripts/show_schema.py` both depend on to see the full schema. Deleting an individual model file breaks any migration or query touching that table; deleting `models/__init__.py`'s imports (without deleting the files) would silently make Alembic blind to those tables even though they still exist as classes.

`db/repositories/` is the SQL-writing layer described above. `user.py` (`UserRepository`) handles user lookup/creation — including a `get_or_create` that uses `INSERT ... ON CONFLICT DO NOTHING` rather than check-then-insert, specifically to survive two `/start` taps racing each other — and preference reads/writes. `cv.py` (`CVRepository`) handles CV row creation and lookup. `repositories/__init__.py` re-exports both and states the layering rule in its own docstring.

### `app/services/`

`onboarding.py` contains `OnboardingService`, the state machine described in detail above — this is the largest and most important file in the Day 3 work. `cv_intake.py` contains `CVIntakeService`, which validates an uploaded file's extension and size cheaply (before any bytes are downloaded from Telegram), then validates the actual file content against its format's magic bytes, then writes it to disk under a UUID filename inside `CV_STORAGE_DIR` — the UUID naming is explicitly there to stop a crafted filename like `../../.env` from escaping the storage directory, and to stop two different users' `resume.pdf` from colliding. `replies.py` defines `BotReply` and `Button`, plain frozen dataclasses with zero Telegram imports — the explicit point being that a service can be tested (as `tests/test_onboarding.py` does) without a bot token, a network connection, or python-telegram-bot installed correctly at all. `services/__init__.py` re-exports the public surface of all three.

### `app/bot/`

`application.py` builds the python-telegram-bot `Application` object from `settings.telegram_bot_token` and calls `register_handlers` on it; this is what `app.main`'s lifespan calls when `TELEGRAM_MODE=polling` and a token is configured. `rendering.py` is the one file allowed to know about both `BotReply` and Telegram's `InlineKeyboardMarkup` — it converts one to the other, keeping that conversion out of both the service layer and the handler functions themselves.

`bot/handlers/__init__.py` (`register_handlers`) wires every command and message type to its handler function, in an order that matters: python-telegram-bot walks handlers in registration order and stops at the first match, so the catch-all text handler (`filters.TEXT & ~filters.COMMAND`) is registered last deliberately, and it excludes commands so that an unrecognized command like `/foo` doesn't get swallowed into the onboarding state machine as if it were a typed answer. `bot/handlers/common.py` holds the two commands with no onboarding awareness (`/help`, `/ping`) plus the global `error_handler`, which is registered via `add_error_handler` and exists so that an unhandled exception anywhere in a handler still results in the user getting an apologetic reply instead of silence — silence being, as its docstring notes, indistinguishable from the bot simply being slow. `bot/handlers/onboarding.py` holds every handler that touches the onboarding flow (`/start`, `/status`, `/restart`, document uploads, photo uploads — rejected explicitly since Day 4 has no OCR — plain text, and button callbacks); every one of them follows the same four-step shape documented at the top of the file and delegates all actual decision-making to `OnboardingService`.

### `app/api/`, `app/schedulers/`, `app/schemas/`, `app/utils/`, `app/workflows/`

These five directories are empty scaffolding — confirmed in Step 2 of this session, before anything was extracted into the repo. `app/api/routes/` exists as an empty nested folder (not even an `__init__.py`) and is presumably where future FastAPI routes will live, using `get_session` as their dependency. The other four (`schedulers`, `schemas`, `utils`, `workflows`) don't even have subdirectories yet. None of them are imported anywhere, so their presence or absence has zero effect on the app today — they are placeholders for later days' work (job ingestion, scheduled matching runs, Pydantic request/response schemas, shared helpers) and should be treated as reserved names rather than dead code to clean up.

### `scripts/`

`show_schema.py` prints the `CREATE TABLE`/`CREATE INDEX` SQL that the current models would generate, using only `Base.metadata` — it needs no live database connection, which makes it useful for reviewing what a migration should contain before writing one. `query.py` (added this session) runs an arbitrary SQL string against `DATABASE_URL` using the app's own engine setup and prints the result as a simple table — the tool behind the `SELECT * FROM users` and `SELECT extname FROM pg_extension` queries run in this conversation. `onboarding_dryrun.py` drives the entire `OnboardingService` flow against the real configured database with a fake, clearly-out-of-range Telegram ID (`9_000_000_001`), printing every bot reply exactly as the earlier flow check did, and cleans its test user up afterward unless `--keep` is passed. All three scripts independently set `WindowsSelectorEventLoopPolicy()` at the top when running on Windows, for the same psycopg/asyncio reason noted under `alembic/env.py` — any new script that opens an async database connection directly (rather than going through a context that already sets this) will need the same guard on Windows.

### `storage/`

Created at runtime by `CVIntakeService.save()`, one subdirectory per user ID, holding the actual uploaded CV bytes under UUID filenames. It is gitignored and does not need to exist ahead of time — the service creates it (`mkdir(parents=True, exist_ok=True)`) on first upload.

### `tests/`

`test_health.py` are HTTP smoke tests against the FastAPI app's three routes, using `TestClient` — described in its own docstring as protecting the Day 1 foundation that every later stage must keep passing. `test_models.py` inspects `Base.metadata` directly rather than connecting to a database, asserting things like every expected table being registered, `telegram_id` being a unique `BIGINT`, and every user-owned table cascading on delete. `test_onboarding.py` is the Day 3 test file: it covers the `onboarding_state` column shape, the `parse_list_input` comma/newline parser (including deduplication and the ten-item cap), the experience/threshold choice tables, and the full `CVIntakeService` validation and storage path logic — using a `pytest` fixture that points `CVIntakeService` at `tmp_path` rather than the real `storage/` directory, which is why running the suite never touches real CV storage. None of the three files need a live database connection; that is deliberate, and is exactly what let `pytest -q` finish in about twelve seconds during Step 7.

## What's safe to edit freely, and what has knock-on effects

Safe to edit with essentially no ripple effect: `app/bot/handlers/common.py` (`/help` text, `/ping` response), the prompt text and button labels inside `OnboardingService._prompt_for` (as long as the `OnboardingState` the prompt is keyed on doesn't change), `docs/CODEBASE_GUIDE.md` itself, and anything inside the five empty scaffolding directories, since nothing imports them yet.

Edit with care because other files depend on the exact shape: `app/db/models/*.py` — any column change needs a matching Alembic migration, and a column rename or removal will break whichever repository, service, or test references it by name. `app/db/session.py`'s `get_session`/`session_scope` signatures — every handler and every future route depends on their exact async-context-manager shape. `app/services/replies.py`'s `BotReply`/`Button` dataclasses — changing their fields means updating `app/bot/rendering.py`'s `to_markup` in lockstep, since that is the only place that reads them. The `OnboardingState` enum values in `app/db/models/user.py` — they are stored as raw strings in the database, so renaming a value requires a migration that rewrites existing rows, not just a code change, or old rows become unreadable by `OnboardingState(user.onboarding_state)`.

Do not edit directly, only extend: any file under `alembic/versions/`. Once a migration has run anywhere (including against the Neon database this project uses), it is a historical record; the correct way to change the schema further is always a new migration via `alembic revision --autogenerate`, never an edit to an old revision file — editing one desyncs whatever database already applied the old version from what the file now claims to do.

## The `storage/` directory

`storage/cvs/` holds the actual bytes of every CV a user has ever uploaded through Telegram, one subdirectory per user ID (`storage/cvs/<user_id>/`). Nothing else lives under `storage/` today. The directory is created lazily — `CVIntakeService.save()` calls `mkdir(parents=True, exist_ok=True)` on first upload — so there is nothing to set up ahead of time, and a fresh checkout of this repository has no `storage/` directory at all until someone uploads a CV.

Every file inside is named with a UUID plus the original extension (`6d33d87cd69a40e185db3347962eb72c.pdf`), never the name the user's file arrived with. This is deliberate, not cosmetic. `CVIntakeService.build_path()` explains it directly: an uploaded filename is attacker-controlled input, and something like `../../.env` would otherwise let a crafted filename escape the storage directory entirely; separately, two different users both naming their file `resume.pdf` would silently overwrite one another on a shared filesystem path. Using a UUID sidesteps both problems at once. The user-facing filename people actually typed is not thrown away — it is kept in the `cvs.file_name` column, which is where anything meant for display or logging should read it from.

The column that connects a database row to a file on disk is `cvs.storage_path` — a full relative path such as `storage\cvs\2\0f56254d657a4196a79359aa2be42c36.pdf`, written once by `CVIntakeService.save()` at upload time and never rewritten afterward. Deleting the database row does not delete the file it points to; the two are separate stores, updated independently, and code that needs both gone must say so explicitly by calling `CVIntakeService.delete(user_id, storage_path)` after removing the row. That method exists specifically because that gap was found and fixed here: it was possible for a `users` row to be deleted (cascading away its `cvs` rows) while the file that row's `storage_path` pointed to sat on disk indefinitely, referenced by nothing. `delete()` closes it safely — it resolves the given path, refuses to act unless the resolved path is inside that specific user's own subdirectory of the configured storage root, and treats an already-missing file as success rather than an error, since the point is the end state (file gone), not the act of unlinking one.

`storage/` must never be committed to Git — it is listed in `.gitignore` — for a reason no other ignored path in this project shares: everything else excluded (`.venv/`, `__pycache__/`, `.pytest_cache/`) is regenerable tooling output, but `storage/` holds real people's CVs, uploaded in confidence through a bot that exists to help them find a job. Committing it would mean checking a stranger's résumé, including whatever personal details it contains, into version control, where it would be effectively permanent even if deleted in a later commit. There is no scenario in this project where a file under `storage/` belongs in the repository.
