# AI Job Hunting Agent

A Telegram bot that reads a candidate's CV, watches job boards on their
behalf, and messages them only when a posting is genuinely worth their
attention.

The point is the last clause. Sending every new posting is a mailing
list, and a mailing list is what candidates already ignore. This system
scores each candidate-job pair on five signals, records why it scored
what it did, and refuses to notify unless the score is high AND was
computed from enough evidence to be worth trusting.

---

## Contents

- [What it does](#what-it-does)
- [Architecture](#architecture)
- [Tech stack, and why](#tech-stack-and-why)
- [Installation](#installation)
- [Environment variables](#environment-variables)
- [Database](#database)
- [Telegram setup](#telegram-setup)
- [Running it](#running-it)
- [Running the tests](#running-the-tests)
- [The end-to-end lifecycle](#the-end-to-end-lifecycle)
- [Operational checks](#operational-checks)
- [Sharing this repository](#sharing-this-repository)
- [Troubleshooting](#troubleshooting)
- [Further reading](#further-reading)

---

## What it does

```text
Telegram user
      |
      v
CV upload  ->  text extraction  ->  LLM profile extraction  ->  profile
                                                                   |
Job boards ->  ingestion  ->  validation  ->  dedup  ->  jobs       |
                                                          |         |
                                            embeddings (both sides) |
                                                          |         |
                                                  pgvector retrieval
                                                          |
                                    five-signal hybrid scoring + ranking
                                                          |
                                          LangGraph workflow + gate
                                                          |
                                            Telegram notification
                                                          |
                                                 user feedback
                                                          |
                                                     PostgreSQL
```

A candidate sends `/start`, answers a few onboarding questions and
uploads a CV. Everything after that is unattended: a nightly run
ingests new postings, embeds them, scores them against every candidate,
and messages the ones that clear the gate. Three buttons on each
message record what the candidate thought.

---

## Architecture

| Layer        | Directory              | Responsibility                                                                              |
| ------------ | ---------------------- | ------------------------------------------------------------------------------------------- |
| HTTP         | `app/main.py`          | FastAPI app, health and readiness endpoints, application lifespan                           |
| Bot          | `app/bot/`             | Telegram handlers. Unwrap an update, call a service, render the reply. No SQL, no decisions |
| Services     | `app/services/`        | Every business rule. Own their transactions, return dicts of counters                       |
| Workflows    | `app/workflows/`       | The LangGraph agent: 8 nodes, 3 conditional edges. Orchestration only                       |
| Repositories | `app/db/repositories/` | Queries. No business logic                                                                  |
| Models       | `app/db/models/`       | SQLAlchemy ORM, 11 models                                                                   |
| Integrations | `app/integrations/`    | The only place a third-party network SDK is imported: Adzuna, Gemini, Telegram              |
| Schemas      | `app/schemas/`         | Pydantic boundary types                                                                     |
| Migrations   | `alembic/`             | 10 migrations, head `c8e2a15f4b93`                                                          |
| Scripts      | `scripts/`             | Entry points and read-only diagnostic checks                                                |

Four rules hold the layering together, and they are enforced by tests
rather than by convention:

- No SQL in handlers.
- No Telegram imports in services.
- No business logic in repositories.
- `app/workflows/` imports no repository. The graph owns decisions; the
  driver owns the database.

Every pipeline entry point has the same shape: a module-level
`async def run_x(...)` that owns its transactions, commits per unit
rather than once at the end, and returns a dict of counters.
`run_ingestion`, `run_enrichment`, `run_job_embedding`, `run_scoring`,
`run_notification_delivery`.

See `docs/FINAL_ARCHITECTURE.md` for the component-by-component version.

---

## Tech stack, and why

| Technology                 | Why this one                                                                                                                                                                                                        |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Python 3.12**            | The NLP and embedding ecosystem lives here                                                                                                                                                                          |
| **FastAPI**                | Async all the way down, which matters because almost every operation is IO-bound: an HTTP call to a job board, an LLM call, a database round trip. Gives `/health` and `/health/ready` and OpenAPI docs for nothing |
| **PostgreSQL**             | One store for relational data and vectors. A separate vector database would mean a job's metadata and a job's embedding could disagree, and nothing would notice                                                    |
| **pgvector**               | Semantic retrieval in the same query planner as everything else. `ORDER BY embedding <=> :vector` is a join away from the job's company, location and skills                                                        |
| **Gemini**                 | CV extraction and embeddings. Structured extraction from unstructured CVs is exactly what an LLM is good at                                                                                                         |
| **LangGraph**              | The nightly run is a state machine with conditional branches, not a linear script. Explicit nodes and edges make the routing testable in both directions                                                            |
| **python-telegram-bot**    | The candidate is already on their phone. No app to install, no login, and inline keyboards make feedback a single tap                                                                                               |
| **Alembic**                | Schema changes reviewed as code                                                                                                                                                                                     |
| **Windows Task Scheduler** | Survives reboots and exposes `LastTaskResult`, which an in-process scheduler does not                                                                                                                               |

The reasoning behind each, including the alternatives that were
rejected, is in `docs/TECHNICAL_DECISIONS.md`.

---

## Installation

Windows / PowerShell is the development environment.

```powershell
git clone <repository-url>
cd AI_JOB_HUNT_AGENT

python -m venv .venv
.\.venv\Scripts\Activate.ps1

pip install -r requirements.txt

Copy-Item .env.example .env
# then edit .env -- see below
```

PostgreSQL 16 with the pgvector extension must be reachable. Verified
against PostgreSQL 16.15 and pgvector 0.6.0.

---

## Environment variables

`.env.example` documents every variable with a placeholder value.
Copy it to `.env` and fill in the five credentials.

`.env` is gitignored and **must never be committed, printed, pasted or
included in an archive**. Five live credentials live in it.

| Variable                           | Purpose                                                                |
| ---------------------------------- | ---------------------------------------------------------------------- |
| `DATABASE_URL`                     | `postgresql+psycopg://user:pass@host:5432/dbname`                      |
| `TELEGRAM_BOT_TOKEN`               | From @BotFather                                                        |
| `TELEGRAM_MODE`                    | `polling` or `disabled`. Anything else is now a fatal error at startup |
| `GEMINI_API_KEY`                   | CV extraction and embeddings                                           |
| `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` | Job ingestion. **These travel in the query string**                    |

Everything else has a default in `app/core/config.py`, and every
default carries a comment explaining the number. Two are worth knowing
before changing anything:

- The five scoring weights must sum to `1.0`. `Settings` refuses to
  construct otherwise, because weights summing to 0.95 make every score
  quietly 5% low while nothing looks broken.
- Changing any weight requires bumping `weights_version`.

---

## Database

```powershell
alembic upgrade head        # 10 migrations, head c8e2a15f4b93
alembic current             # confirm
alembic check               # confirm the ORM and the schema agree
```

`alembic check` is worth running after any model change. Until Day 12
the models and the migrated schema had drifted apart in four places,
and `alembic revision --autogenerate` would have produced a migration
dropping both HNSW vector indexes, the unfinished-runs index and the
job deduplication constraint. See `docs/TECHNICAL_DECISIONS.md`.

The pgvector extension must exist in the target database:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

---

## Telegram setup

1. Message [@BotFather](https://t.me/BotFather), send `/newbot`, follow
   the prompts.
2. Put the token in `.env` as `TELEGRAM_BOT_TOKEN`.
3. Set `TELEGRAM_MODE=polling`.
4. Start the API (below) and send `/start` to the bot.

Only one process may poll a bot at a time. Telegram answers the second
one with HTTP 409, and updates consumed by the wrong process are
acknowledged and gone.

---

## Running it

```powershell
# API + bot
python run.py

# One agent run, by hand
python -m scripts.run_agent --dry-run          # scoring rehearsal, no writes
python -m scripts.run_agent --user-id 2        # one candidate
python -m scripts.run_agent                    # the real thing

# Individual stages
python -m scripts.ingest_jobs
python -m scripts.embed_jobs
python -m scripts.enrich_jobs
python -m scripts.score_jobs
python -m scripts.send_test_notification
```

Scripts run as `python -m scripts.name`, never
`python scripts/name.py`.

`--dry-run` is a **scoring** rehearsal, not a pipeline rehearsal. It
scores whatever is already in the database; ingestion and embedding are
skipped rather than simulated, because calling them would hit the job
board and spend embedding quota. Read it as "what would scoring say
about today's rows", never as "what tonight's run would do".

### The scheduler

```powershell
powershell -File scripts/schedule_agent.ps1 -SelfTest   # arms nothing
powershell -File scripts/schedule_agent.ps1             # registers the task
```

`-SelfTest` proves the interpreter, the working directory, the log path
and the command line. It does not prove Windows starts the task. The
check that observes whether a run actually happened is
`scripts/check_run_freshness.py` — see below.

---

## Running the tests

```powershell
python -m pytest -q
```

706 tests, no network access and no database required. The suite is
hermetic: `tests/conftest.py` pins a test environment before any
application import, so the tests cannot reach the real Telegram bot,
the real database or any real credential.

For the 23 integration tests, give it a scratch database:

```powershell
createdb jobagent_test
psql -d jobagent_test -c "CREATE EXTENSION IF NOT EXISTS vector"
$env:TEST_DATABASE_URL = "postgresql+psycopg://user:pass@localhost:5432/jobagent_test"
python -m pytest -q                # 729 tests
```

**The scratch database is truncated before every integration test.**
Never point `TEST_DATABASE_URL` at the development database.

Without that variable the integration tests **skip**, and a skipped
test prints a green `s` that reads a lot like a pass. `pytest -q`
reports the two figures separately for that reason.

`pytest-asyncio` is not installed and must not be. Async tests are
synchronous functions driving a coroutine with `asyncio.run()`.

---

## The end-to-end lifecycle

1. **Onboarding** — `/start`, target roles, preferred locations,
   CV upload. State machine in `app/services/onboarding.py`.
2. **CV intake** — the file is downloaded and stored;
   `cvs.extraction_status` starts at `pending`.
3. **Extraction** — text layer out of the PDF or DOCX, then Gemini
   turns it into a structured profile. A new `cv_versions` row every
   time, never an overwrite. An extraction that parses but says nothing
   is `empty`, and leaves the existing profile alone.
4. **Embedding** — the CV version is embedded as a `RETRIEVAL_QUERY`,
   jobs as `RETRIEVAL_DOCUMENT`. The distinction is real: the same text
   under the two task types comes back at cosine 0.861.
5. **Ingestion** — job board search, Pydantic normalization, validation,
   two-path deduplication (source id, then content hash), insert.
   Every fetched record leaves through exactly one counter, and the
   funnel is asserted.
6. **Enrichment** — Gemini extracts required skills from a job
   description, one job per call.
7. **Scoring** — five signals per pair, weighted, renormalised by the
   weight actually covered, multiplied by a posting-quality factor.
   Written to `recommendations` with every input and reason.
8. **Notification** — three inclusive gates: score, raw semantic
   similarity, and coverage. All three, all `>=`.
9. **Feedback** — Interested / Save / Not Relevant, one row each,
   contradictions kept.

`docs/END_TO_END_VERIFICATION.md` is a real trace through this with
timings and row counts.

---

## Operational checks

All read-only. None of them writes to the database.

```powershell
python -m scripts.check_run_freshness            # did the nightly run happen?
python -m scripts.verify_archive <path.zip>      # is this zip safe to share?
python -m scripts.check_indexes
python -m scripts.notification_constraints_check # proves the index FIRES
python -m scripts.scorable_targets_check
python -m scripts.show_schema
```

`check_run_freshness` exits 0 fresh, 1 stale, 2 no rows at all, 3 could
not tell. The three-way split matters: a new deployment and a dead
scheduler both have no recent run, and only one is an incident.

---

## Sharing this repository

**Build the archive with the script. Every credential incident in this
project's history came from not doing that.**

```powershell
powershell -File scripts/pack.ps1 -SelfTest    # first, once
powershell -File scripts/pack.ps1
powershell -File scripts/verify_archive.ps1 <the-zip-it-produced>
```

`pack.ps1` builds from `git archive`, so `.env`, `storage/`, `.git/`,
`*.zip` and `*.pdf` are excluded by construction. `verify_archive.ps1`
applies the same rules to **any** zip, including one somebody made with
Explorer — which is what every incident actually was.

A valid archive can still be a useless one. `git archive` cannot
include what was never committed, so run
`git status --short --untracked-files=all` and decide about every `??`
line before packing.

---

## Troubleshooting

**`ValueError: Day 8 scoring weights must sum to 1.0`**
A `WEIGHT_*` override in `.env` does not total 1. Fix the weights and
bump `weights_version`.

**`ValueError: TELEGRAM_MODE=... is not recognised`**
Use `polling` or `disabled`. `webhook` is rejected by name because no
webhook route exists — the API would run and never receive an update.

**`RuntimeError: Database is not configured`**
`DATABASE_URL` is missing, or `init_engine()` was never called. Scripts
call it themselves; a new entry point must too.

**Telegram 409 Conflict**
Two processes are polling the same bot. Stop one. Before Day 12 the
test suite was one of them.

**`sqlalchemy.exc.MissingGreenlet`**
A lazy relationship load under an async session. Load it explicitly or
insert by foreign key.

**`CompileError: Unconsumed column names`**
A counters dict was compiled into an UPDATE against a table missing
those columns. This is why the integration tests exist.

**Gemini 429 on the first call**
The free tier is a daily quota and it is small. A full enrichment pass
is roughly 97 calls and 27–84 minutes.

**Adzuna calls returning nothing useful**
The free tier is about 1,000 calls per month. Ingestion runs record
their spend in `ingestion_runs`; failed calls are spent without
incrementing `pages_fetched`, so the run figure is exact and the total
is not.

---

## Further reading

| Document                          | What it covers                                                        |
| --------------------------------- | --------------------------------------------------------------------- |
| `CLAUDE.md`                       | **Read first.** Decisions that look like bugs, and the working method |
| `docs/FINAL_ARCHITECTURE.md`      | Every component and its responsibility                                |
| `docs/TECHNICAL_DECISIONS.md`     | Why each technology, and what was rejected                            |
| `docs/MATCHING_AND_SCORING.md`    | The scoring formula as implemented, with its known asymmetry          |
| `docs/TEST_RESULTS.md`            | Actual test results, and what is NOT verified                         |
| `docs/END_TO_END_VERIFICATION.md` | A real trace, stage by stage                                          |
| `docs/MVP_LIMITATIONS.md`         | What this does not do                                                 |
| `docs/MANAGER_DEMO.md`            | A 15-minute demonstration script                                      |
| `docs/CODEBASE_GUIDE.md`          | Directory-level tour                                                  |
| `docs/Day_*.md`                   | The build records, in order                                           |
