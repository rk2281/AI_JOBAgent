# Final Architecture

The system as built, component by component. Read
`docs/CODEBASE_GUIDE.md` for the directory tour; this is about
responsibilities and the boundaries between them.

---

## The whole thing

```text
                        Telegram user
                              |
              /start, CV upload, button taps
                              |
                              v
+-----------------------------------------------------------+
|  app/bot/          handlers: unwrap update, call service,  |
|                    render reply.  No SQL. No decisions.    |
+-----------------------------------------------------------+
                              |
+-----------------------------------------------------------+
|  app/main.py       FastAPI: lifespan owns the engine and   |
|                    the bot; /health, /health/ready         |
+-----------------------------------------------------------+
                              |
+-----------------------------------------------------------+
|  app/services/     every business rule.  Own their         |
|                    transactions.  Return dicts of counters |
+-----------------------------------------------------------+
       |                      |                        |
       v                      v                        v
+--------------+   +--------------------+   +--------------------+
| app/         |   |  app/db/           |   |  app/workflows/    |
| integrations/|   |  repositories/     |   |  LangGraph:        |
| Adzuna       |   |  queries only      |   |  8 nodes,          |
| Gemini       |   +--------------------+   |  3 conditional     |
| Telegram     |            |               |  edges.            |
+--------------+            v               |  Imports NO        |
                   +--------------------+   |  repository        |
                   |  PostgreSQL 16     |   +--------------------+
                   |  + pgvector        |
                   |  15 tables         |
                   +--------------------+
                              ^
                              |
                   +--------------------+
                   |  Task Scheduler    |
                   |  -> run_agent.py   |
                   +--------------------+
```

## Layer rules

Four rules, enforced by tests rather than convention:

1. **No SQL in handlers.** A handler unwraps a Telegram update, opens a
   session, asks a service what to say, and renders it.
2. **No Telegram imports in services.** A service returns a `BotReply`;
   the handler decides how Telegram renders it. This is what lets every
   service be tested without the bot.
3. **No business logic in repositories.** They are queries.
4. **`app/workflows/` imports no repository.** The graph owns
   decisions; the driver owns the database.

Rule 4 is the one that looks arbitrary and is not. `resolve_targets`
needs data, so it calls a _service_ that owns its own transaction and
returns plain ints — no ORM instance, no lazy relationship and no
session ever crosses back into graph state. An exception here would be
a hole to grow into.

`app/integrations/` is the only place a third-party **service** SDK is
imported. This is about vendor network clients, not every third-party
package: `sqlalchemy` lives in repositories, `pydantic` in schemas.

## Entry-point shape

Every pipeline stage is a module-level `async def run_x(...)` that owns
its transactions, **commits per unit rather than once at the end**, and
returns a dict of counters:

`run_ingestion`, `run_enrichment`, `run_job_embedding`, `run_scoring`,
`run_notification_delivery`.

Committing per unit is why an interrupted 40-minute enrichment pass
keeps the work it finished.

---

## Components

### `app/main.py`

FastAPI app and lifespan. The lifespan creates the database engine,
starts the Telegram application when `TELEGRAM_MODE=polling`, and
disposes both on shutdown. `/health` is liveness; `/health/ready`
actually reaches the database and reports what it found.

### `app/bot/`

`application.py` builds the bot and registers handlers.
`handlers/onboarding.py` runs the signup state machine,
`handlers/profile.py` shows what the system knows,
`handlers/feedback.py` handles the three buttons.

Feedback handlers deliberately **leave the keyboard on the message**,
unlike onboarding which strips it. An onboarding button carries an
_answer_ to a question the flow has moved past; a feedback button
carries an _opinion_ about one job, and that means the same thing
whenever it is expressed.

### `app/services/`

Where every rule lives. Notable members:

- `cv_intake` / `cv_text` / `cv_extraction` — download, text layer,
  structured profile. Extraction opens two short transactions with the
  network call **between** them, so no session is held across an LLM
  call. A claim protocol stops two workers extracting one CV.
- `job_ingestion` — search, normalize, validate, deduplicate, insert,
  retire. Every fetched record leaves through exactly one counter and
  the funnel is asserted.
- `job_embedding` / `cv_embedding` / `embedding_text` — what text gets
  embedded, and the bookkeeping that makes a failure visible.
- `job_enrichment` — required skills out of a description, **one job
  per call**. Batching was rejected: a batched response that returns
  eight objects in a different order stores job 3's skills against job
  5, with no dimension check to notice.
- `scoring_signals` / `scoring` / `job_scoring` — the five signals,
  the combination, and the run.
- `notification_delivery` — the gate, and the delivery loop.
- `feedback` — a tap into a row and a sentence.

### `app/workflows/`

`state.py` (typed state and `build_run_summary`), `nodes.py` (the
eight), `routing.py` (three routers, each publishing the values it can
return), `graph.py` (assembly and the tracing guard).

```text
START -> resolve_targets -+-> discover_jobs -> embed_jobs -> enrich_jobs
                          |                                      |
                          |                                      v
                          |                              score_and_rank
                          |                                      |
                          |                    +-----------------+
                          |                    v
                          |            decide_notification -+-> notify -+
                          |                                 |           |
                          +---------------------------------+-----------+
                                                            v
                                                        finalise -> END
```

`build_run_summary(state) -> dict` is the seam: pure, separately
tested, and returning exactly the fields the `agent_runs` row holds.
`test_every_summary_key_has_a_column` fails if a key has nowhere to
land — a field with no column is silently dropped on write while every
other test stays green.

### `app/db/`

`session.py` owns the engine; nothing else creates one. `session_scope()`
commits on exit, which is what keeps one Telegram update atomic: a
handler that writes a CV row and then advances onboarding state either
does both or neither.

11 models, 15 tables. Two tables record _runs_ (`ingestion_runs`,
`scoring_runs`, `embedding_runs`, `agent_runs`) using the same shape: a
row opened before the work and completed after, so a process killed
mid-flight leaves evidence rather than silence.

### `app/integrations/`

`adzuna.py`, `gemini.py`, `gemini_embeddings.py`,
`gemini_enrichment.py`, `telegram.py`, and `http_errors.py` — the
last because an exception string from an HTTP client can contain a URL,
and Adzuna's credentials travel in the query string.

### `scripts/`

Entry points (`run_agent`, `ingest_jobs`, `score_jobs`, ...) and
read-only diagnostics. The diagnostics are the interesting half:
`notification_constraints_check` proves the partial unique index
**fires** rather than that it exists; `asymmetry_isolate` measures the
scoring asymmetry without importing `combine()`; `check_run_freshness`
observes whether the nightly run happened; `verify_archive` refuses an
unsafe zip.

---

## Data model, in brief

```text
users ──┬── user_preferences
        ├── cvs ── cv_versions (embedding vector(768))
        ├── profiles (active_cv_version_id -> cv_versions)
        ├── recommendations ── (job_id -> jobs)
        ├── notifications
        └── user_feedback

jobs ───┬── job_skills ── skills
        └── embedding vector(768)

ingestion_runs ── ingestion_rejects
scoring_runs
embedding_runs
agent_runs
```

Three things about this shape are load-bearing:

**The CV embedding lives on `cv_versions`, not `profiles`.** Each
version's embedding describes that version's text and is never updated,
so it cannot go stale. `profiles.active_cv_version_id` points at the
current one.

**`notifications` is an ATTEMPT table.** At most one `SENT` row per
`(user, job)` — enforced by a partial unique index — and any number of
`pending`/`failed`. The old unique constraint made a _failure_
permanent, locking a user out of a job after one outage.

**Signal columns on `recommendations` are nullable, and NULL means
abstain.** Defaulting them to 0.0 destroys the abstain model.

## Where the time goes

Everything expensive is a network call. The database work in a full
end-to-end run is under 400 ms; a full enrichment pass is 27–84 minutes
against Gemini's free tier. That ratio is why enrichment is a separate
stage with its own quota accounting, and why `--dry-run` skips the
stages that spend quota rather than simulating them.
