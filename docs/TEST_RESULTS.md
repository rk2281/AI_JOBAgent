# Test Results

Recorded 2026-09-05. Every figure below was produced by executing the
command shown. Nothing here is estimated.

Where something could not be executed, it says `NOT VERIFIED` and why.
A `NOT VERIFIED` line is more useful than an optimistic `PASS`, and
this document is the place the distinction is kept.

---

## Suite totals

```text
python -m pytest -q                       # no database configured
706 passed, 23 skipped, 4 warnings in 2.36s

$env:TEST_DATABASE_URL = "postgresql+psycopg://.../jobagent_test"
python -m pytest -q
729 passed, 4 warnings in 4.94s
```

|                            | Count                              |
| -------------------------- | ---------------------------------- |
| Total tests                | 729                                |
| Passed (with database)     | 729                                |
| Passed (without database)  | 706                                |
| Skipped (without database) | 23                                 |
| Failed                     | 0                                  |
| Errors                     | 0                                  |
| Warnings                   | 4                                  |
| Execution time             | 4.94s with database, 2.36s without |

The four warnings are all `PTBDeprecationWarning` from
`python-telegram-bot` about `retry_after` becoming a `timedelta` in a
future major version. Two call sites, in `tests/test_telegram_notifier.py`.

### Read the two numbers together

The 23 integration tests **skip** when `TEST_DATABASE_URL` is unset,
and `pytest -q` renders a skip as `s`, which at a glance looks like a
pass. A default run is therefore "the integration tests did not run",
not "the integration tests passed". `tests/integration/test_harness.py`
makes a _misconfigured_ URL loud; nothing inside pytest can make an
_absent_ database loud, which is why both figures are recorded here.

### Environment

|                             |                                         |
| --------------------------- | --------------------------------------- |
| Python                      | 3.12.3                                  |
| PostgreSQL                  | 16.15                                   |
| pgvector                    | 0.6.0                                   |
| OS during this verification | Ubuntu 24.04 (see caveat below)         |
| Network                     | No egress to Adzuna, Gemini or Telegram |

**The project's own environment is Windows / PowerShell.** This
verification ran on Linux, so anything Windows-specific —
`schedule_agent.ps1`, `pack.ps1`, `verify_archive.ps1`, the
`WindowsSelectorEventLoopPolicy` branches — was **NOT VERIFIED** here.
The Python those wrappers call was verified.

---

## Where the tests came from

|                           | Before Day 12 | After |
| ------------------------- | ------------- | ----- |
| Unit tests                | 664           | 706   |
| Integration tests         | 0             | 23    |
| Total                     | 664           | 729   |
| Tests needing a database  | 0             | 23    |
| Tests needing the network | **3**         | 0     |

The three that needed the network were `tests/test_health.py`, and they
did not merely _need_ it — they authenticated as the production bot and
started consuming its updates. See `docs/TECHNICAL_DECISIONS.md`.

---

## Component status

| Component                | Status       | How it was established                                |
| ------------------------ | ------------ | ----------------------------------------------------- |
| FastAPI app              | PASS         | `tests/test_health.py`, real `TestClient` lifespan    |
| Migrations               | PASS         | `alembic upgrade head` on an empty database, 10 of 10 |
| ORM / schema agreement   | PASS         | `alembic check` → no operations detected              |
| PostgreSQL               | PASS         | 23 integration tests, real connection                 |
| pgvector storage         | PASS         | 768-dim vectors written and read back                 |
| pgvector retrieval       | PASS         | Ordering asserted against known angles                |
| CV text extraction       | PASS         | Real `.docx` generated and parsed by `python-docx`    |
| CV profile extraction    | PARTIAL      | Real code path; the model call is a stand-in          |
| Profile persistence      | PASS         | `cv_versions`, `profiles`, `skills` all asserted      |
| Job ingestion            | PASS         | Real service, fixture `JobSource` via the Protocol    |
| Job validation           | PASS         | Reject rows and reasons asserted                      |
| Deduplication            | PASS         | Both paths: source id, and content hash               |
| Embedding generation     | NOT VERIFIED | Gemini unreachable — see below                        |
| Matching (5 signals)     | PASS         | All five, plus every abstain combination              |
| Scoring                  | PASS         | Weighted sum reconstructed from stored columns        |
| Ranking                  | PASS         | Ranks 1..n, dense, descending                         |
| LangGraph workflow       | PASS         | Compiled graph really invoked                         |
| `agent_runs` persistence | PASS         | Two writes, open then finish                          |
| Notification gate        | PASS         | Both directions, at exact boundaries                  |
| Notification delivery    | PARTIAL      | Full loop; the socket is a stand-in                   |
| Feedback                 | PASS         | Real callback string → real rows                      |
| Scheduler                | NOT VERIFIED | Windows Task Scheduler — see below                    |
| Logging                  | PARTIAL      | Redaction unit-tested; no live log reviewed           |
| Error handling           | PASS         | Corrupt CV, empty CV, malformed jobs, unknown user    |
| Idempotency              | PASS         | Ingestion, scoring and notification, each re-run      |

---

## Integration tests, by pipeline

All 23 executed against PostgreSQL 16.15 with pgvector 0.6.0.

| File                              | Tests | Covers                                                                 |
| --------------------------------- | ----- | ---------------------------------------------------------------------- |
| `test_harness.py`                 | 1     | Connection and extension are real                                      |
| `test_candidate_pipeline.py`      | 3     | CV → extraction → profile; empty CV; corrupt CV                        |
| `test_job_pipeline.py`            | 4     | Funnel; content-hash dedup; idempotency; malformed page                |
| `test_recommendation_pipeline.py` | 5     | Retrieval, ordering, scoring, ranking, re-scoring                      |
| `test_agent_pipeline.py`          | 5     | Graph run, summary columns, notify branch, empty run, coverage refusal |
| `test_feedback_pipeline.py`       | 5     | Tap, repeat tap, contradiction, unknown user, bad callback             |

Two of these deserve calling out.

**`test_the_notify_branch_delivers_and_records_an_attempt`** is the
first time the notify branch has executed outside a stub. On live data
`notify_eligible` is 0 and always has been, so the branch existed,
routed correctly and had never carried a message.

**`test_every_summary_key_lands_in_a_column`** executes the write that
produced `CompileError: Unconsumed column names` on Day 10. That defect
survived two parts and a commit because the suite had no database and
every live check had been `--dry-run`.

---

## End-to-end run

```text
python -m scripts.e2e_verify --database-url postgresql+psycopg://.../jobagent_test
```

17 stages, all OK, 370.6 ms total (388.5 ms on a re-run). Full trace in
`docs/END_TO_END_VERIFICATION.md`.

---

## What was NOT verified, and why

### Adzuna — NOT VERIFIED

No egress to `api.adzuna.com`. Ingestion was exercised through a
fixture source satisfying the same `JobSource` Protocol production
uses, so every rule downstream of the response ran for real. What was
not exercised is Adzuna's own response parsing in
`app/integrations/adzuna.py`, which `tests/test_job_ingestion.py`
covers with recorded payloads.

Independently of the network: **the credentials have leaked three times
and have never been rotated**, so the account's remaining quota is
unknown — the only authority on it is Adzuna's dashboard, which needs
the leaked credentials to read.

### Gemini — NOT VERIFIED

No egress to the Gemini API. Both uses are affected:

- **CV extraction** — the surrounding code path is real (claim,
  text extraction, version row, profile update, skill catalog,
  supersession); `extract_profile` returned a fixed `CVProfile`.
- **Embedding generation** — not exercised at all. Vectors were
  constructed at known angles. Storage, dimensionality, indexing and
  retrieval are real; the numbers going in were not model output.

This matters most for the prediction on record: after a full enrichment
pass, `abstain_experience` should fall to roughly **60**, not 0. That
prediction remains untested.

### Telegram — NOT VERIFIED

No egress to `api.telegram.org`. Verified without it: message
rendering, the three gates, candidate selection, attempt rows, status
transitions, duplicate suppression via the partial unique index,
deactivation on an unreachable chat, and error classification. Not
verified: that a message arrives, that inline keyboards render, that a
real tap reaches the backend.

`scripts/e2e_verify.py --with-telegram` closes this on a machine with a
working token.

### Scheduler — NOT VERIFIED

Windows Task Scheduler does not exist on the machine this ran on. The
underlying entry point (`scripts/run_agent.py`) was executed directly
and `agent_runs` rows were written.

This has never been verified on the project's own machine either.
CLAUDE.md records that `-SelfTest` "does not prove Windows starts the
task, that its credentials can read the repo, or that a log lands with
a real exit code in it." That is still true. What Day 12 adds is
`scripts/check_run_freshness.py`, which observes whether a run
_happened_ rather than whether the task was _registered_ — all three of
its states were exercised against real rows:

```text
EMPTY (exit 2)   no agent_runs rows
FRESH (exit 0)   newest run 4.0 hours old
STALE (exit 1)   newest run 40.0 hours old
```

### PowerShell scripts — NOT VERIFIED

`pack.ps1` was not re-run (no PowerShell, no `.git` directory in the
archive). `verify_archive.ps1` was not executed; the Python it wraps
was, including its self-test:

```text
python -m scripts.verify_archive --self-test
self-test result   PASS      # 8 cases
```

and against the archive this work arrived in:

```text
python -m scripts.verify_archive AI_JOB_HUNT_AGENT.zip
result             FORBIDDEN ENTRIES FOUND
  .env       AI_JOB_HUNT_AGENT/.env
  storage/   27 entries, 21 of them PDFs
  *.pdf      21 entries
EXIT 1
```

---

## Data quality

Beyond "the code returned without raising":

| Check                                        | Result                                                      |
| -------------------------------------------- | ----------------------------------------------------------- |
| Extracted profile matches the CV             | PASS — 5 skills, title, location, 1.20y computed from dates |
| Profile skills are normalized catalog keys   | PASS — and the version keeps original spellings             |
| Experience computed, not model-supplied      | PASS — from structured start/end fields                     |
| Jobs stored with a content hash              | PASS — all rows non-NULL                                    |
| Embeddings correctly shaped                  | PASS — 768 dimensions read back                             |
| Similarity numbers meaningful                | PASS — match cos(θ) to 1e-4                                 |
| Ranking sensible                             | PASS — ML job above the accountancy job                     |
| Recommendation columns reconstruct the score | PASS — weighted sum to 1e-12                                |
| Abstains stored as NULL                      | PASS — never 0.0                                            |
| Notification text useful                     | PASS — title, company, location, score, reasons             |
| Missing fields omitted, not rendered empty   | PASS                                                        |

---

## Known gaps in the testing itself

- **The E2E stops one layer short of three networks.** Any claim about
  Adzuna, Gemini or Telegram behaviour in production rests on unit
  tests over recorded payloads.
- **The graph cannot run non-dry without Telegram.** The `notify` node
  calls `run_notification_delivery()`, which constructs a real
  `TelegramNotifier` with no injection seam.
- **No load or concurrency testing.** `concurrent_claim_dryrun.py`
  exists but fires real Gemini calls and is misnamed.
- **`users_skipped_no_profile` still cannot be non-zero through
  `run_agent.py`**, so a zero there remains evidence of nothing.
- **Windows-specific code paths are untested on Windows** by this run.
