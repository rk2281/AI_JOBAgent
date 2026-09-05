# Technical Decisions

Why each piece is what it is, and what was rejected. The Day 12
findings are at the end, since those are decisions made during this
phase rather than inherited.

---

## Why FastAPI

Almost every operation is IO-bound — an HTTP call to a job board, an
LLM call, a database round trip. Async is the shape of the problem, and
FastAPI is async to the floor. Free: `/health`, `/health/ready`,
OpenAPI docs, and Pydantic validation at the boundary, which is where
job-source responses arrive.

_Rejected:_ Flask (sync, and the async story is bolted on); Django
(an ORM, admin and template layer this has no use for).

## Why PostgreSQL

One store for relational data and vectors. A job's company, location,
required skills and embedding live in the same row, so a semantic
search is one join away from a location filter.

_Rejected:_ a dedicated vector database alongside a relational one.
That means two stores that can disagree about the same job, with
nothing noticing when they do. For 99 jobs it would be all cost.

## Why pgvector

Semantic retrieval inside the same query planner as everything else.
`ORDER BY embedding <=> :vector LIMIT n` with an HNSW index, and the
result set is already joined to the job.

One sharp edge, learned the hard way: HNSW keeps only `ef_search`
candidates in flight, so an `ef_search` below the `LIMIT` returns fewer
rows than asked for **with no error**. `run_scoring` scales `ef_search`
to the pool size. At 99 rows the planner picks a sequential scan and
this never bites; it would begin biting silently once the table
outgrows that — the worst possible time to find out.

## Why embeddings at all

Keyword matching cannot tell that "NLP Engineer" and "Machine Learning
Engineer, Language Team" are the same job, or that "Java" is not
"JavaScript". Embeddings put both sides in one space where closeness is
arithmetic.

The task-type distinction is not decorative: a CV is embedded as a
`RETRIEVAL_QUERY` and jobs as `RETRIEVAL_DOCUMENT`, because a CV is the
thing doing the searching. Measured on the live API, the same text under
the two task types comes back at cosine 0.861247.

## Why traditional matching _as well_

Semantic similarity is confidently wrong in exactly the ways that
matter to a job seeker. It cannot tell Delhi from Chennai, one year of
experience from ten, or a job requiring Python from one merely
mentioning it. Those are the constraints candidates actually filter on.

## Why hybrid scoring, with abstention

Five signals, weighted, renormalised by the weight actually covered.
The abstain model exists because the alternative — scoring a missing
signal 0.0 — ranks jobs by the quality of their _descriptions_ rather
than their _fit_, and says nothing about having done so.

Full treatment, including the known asymmetry, in
`docs/MATCHING_AND_SCORING.md`.

## Why LangGraph

The nightly run is a state machine with conditional branches, not a
linear script: no scorable users stops early, no qualifying
recommendations skips notification. Explicit nodes and edges make the
routing testable **in both directions**, which is why the notify edge
was proven before it ever carried a message.

`langgraph` lives in `app/workflows/`, imported by exactly one module
(`graph.py`), asserted by a test. Not `app/integrations/` — that
directory is for anything making a network call on someone else's
credentials, which langgraph does not.

_Rejected:_ a plain async function with `if` statements. It would work.
It would also make "which paths exist" a matter of reading the function
rather than a graph a test can walk.

**Tracing fails closed.** `assert_tracing_disabled()` is the first
statement of `build_graph()`. It raises rather than warns, because a
warning about telemetry is read after the run that already sent the
data — and graph state is CV-derived profile text. It reports variable
NAMES only; two of them are credentials.

## Why Telegram first

The candidate is already there. No app to install, no account to
create, no email to ignore. Inline keyboards make feedback one tap, and
file upload is native — which matters, because the first thing the
system needs is a CV.

## Why a scheduler at all

The value proposition is "you stop checking job boards". That requires
something to check them for you, nightly, without anyone present.

Windows Task Scheduler over APScheduler: it survives reboots, runs
without the API process being up, and exposes `LastTaskResult`. An
in-process scheduler dies with the process that hosts it, and a laptop
that slept through 2am is the normal case, not the exception.

The divergence from the plan's APScheduler was pre-authorised.
Diverging _silently_ was not, and the argument was written five days
late — which left a defensible decision looking identical to an
unexamined one.

## Why not an LLM for every decision

Cost, latency, and reproducibility. Scoring 3 candidates against 99
jobs is 297 pairs; at one LLM call each that is 297 calls per nightly
run against a free tier granting roughly one call before 429.

The deeper reason is explicability. A user asking "why was I shown
this?" gets five numbers, five reasons and a stored fingerprint of the
inputs. An LLM would produce a sentence that sounds like a reason and
cannot be reproduced, audited, or compared against last week's.

**Deterministic where the rule is knowable; LLM where it is not.**
Location matching, experience ranges, title overlap and score
arithmetic are rules. Turning a free-text CV into structured fields, and
reading required skills out of prose, are not — that is what the LLM is
for.

Even there the boundary is drawn tightly: the model is asked for
structured start/end years and months, and **the arithmetic on them is
ours**. A number the model produced could not be explained to a user
asking "why three years?", nor reproduced on a re-run.

---

## Day 12 decisions

### The ORM metadata was four objects out of date, and autogenerate would have dropped them

`alembic check` had apparently never been run. Against a schema built
from the ten migrations it proposed:

```text
remove_index      ix_agent_runs_unfinished
remove_index      ix_cv_versions_embedding_hnsw
remove_index      ix_jobs_embedding_hnsw
remove_constraint uq_job_content_hash
add_index         ix_jobs_content_hash
```

All four exist in the database and are created by migrations; none was
declared in the models. Two were created with raw `op.execute`, one is
a partial index, and one replaced a plain index the model still
declared (`content_hash` moved from index to unique constraint in
migration `d7a3f1c92b40`, and the model was never updated).

Autogenerate compares the database against the metadata, so an object
the metadata does not know about reads as one somebody dropped by hand
— and the generated migration drops it for real. **Dropping
`uq_job_content_hash` removes ingestion's duplicate defence with no
test failing**, since the suite has no database. The symptom would be
duplicate jobs weeks later.

_Chosen:_ declare all four in `__table_args__`. No DDL changes; the
objects already exist. `alembic check` now passes.

_Rejected:_ an `include_object` hook in `env.py` filtering them out.
That hides them, which means a future _genuine_ change to them is
hidden too.

### The test suite was driving the production Telegram bot

`tests/test_health.py` builds `TestClient(app)` as a context manager,
which runs the lifespan, which found `TELEGRAM_MODE=polling` and a real
token and called `Application.initialize()` (live `getMe`) and
`updater.start_polling()` (live `getUpdates`).

Telegram permits one `getUpdates` consumer per bot. A `pytest` run
therefore raced the real bot: the loser got 409, and any update the
test process won was acknowledged and gone. **A user's CV upload
arriving during a test run could vanish.** All three tests passed
throughout, on every machine with a network and a populated `.env`.

_Chosen:_ `tests/conftest.py` pins a hermetic environment before any
`app.*` import. pytest imports conftest before test modules, and
pydantic-settings ranks OS environment above `.env`.

_Rejected:_ detecting pytest inside `lifespan`. That makes the tested
startup path differ from the shipped one, and the same branch would
mask a real misconfiguration in production.

_Rejected:_ patching `test_health.py` alone. It leaves the landmine
armed for the next `TestClient` test.

The guard proving conftest took effect lives in
`tests/test_test_environment.py`, **not** in conftest — pytest imports
conftest but does not collect tests from it, so written there the guard
ran zero times while the suite reported clean. The same mistake it
exists to catch, made while writing it.

### An unrecognised `TELEGRAM_MODE` is now fatal

`app/main.py` starts the bot on exactly one value and does nothing for
every other string. So `TELEGRAM_MODE=poling` started the API cleanly,
served `/health` as `ok`, reported `telegram.configured: true`, and
never answered a message. Nothing logged anything.

`webhook` is rejected by name with its own message, because the generic
"not recognised" would read as _typo_ for what is really a feature
request — there is no webhook route anywhere.

### Integration tests exist, and they skip loudly-ish

23 tests requiring a real PostgreSQL, skipped when `TEST_DATABASE_URL`
is unset. Migrations run through the alembic **CLI in a subprocess**,
because that is the command a human runs; an in-process call can
succeed against an `env.py` the CLI would fail on.

A skipped test prints `s`, which reads like a pass. Nothing inside
pytest can make an absent database loud, so `docs/TEST_RESULTS.md`
records both figures — and `tests/integration/test_harness.py` makes a
_misconfigured_ URL fail in those words rather than confusingly, in
test three.

### The archive verifier duplicates pack.ps1's rules on purpose

Rules in Python (testable, runnable, cross-platform) with a thin `.ps1`
wrapper so the muscle memory from `pack.ps1` works.

A safety list written twice drifts, and the copy that drifts is the one
nobody runs. So `tests/test_verify_archive.py` parses the `Label`
entries out of `pack.ps1` and fails when the two disagree — a drift
**detector** rather than shared code, chosen because rewriting
`pack.ps1` to import from Python would put a proven, incident-hardened
script at risk to save a duplicated list.

Three exit codes, not two: `0` clean, `1` forbidden, `2` could not
read. "This archive is unsafe" and "I could not tell" are different
answers, and a wrapper treating any non-zero as unsafe still behaves
correctly while one treating any non-one as safe does not.

### Freshness is measured over `agent_runs`, not over the scheduler

One query against the table the run itself writes, so it is independent
of the scheduling mechanism. It would notice the task being disabled,
the laptop being off, the venv path going stale, the task running as a
user who cannot read the repository, or the task being deleted — all of
which look identical from here, correctly, because the question is "did
the work happen".

Three states, not two: `FRESH`, `STALE`, `EMPTY`. A new deployment and
a dead scheduler both have no recent run and only one is an incident,
and telling somebody to check a scheduled task that was never
registered wastes the hour in which the real problem is still
happening.

The window is 26 hours rather than 24, because a nightly run is always
almost exactly one period old and a 24-hour window would alert most
mornings. An alert that fires when nothing is wrong is an alert that
gets switched off. The comparison is `<=`, tested at exactly the
boundary.

### The abstention asymmetry was documented, not changed

The Day 12 brief asks whether renormalisation can produce a misleadingly
high score. It can. It is intentional in mechanism and undecided in
policy, and `scripts/asymmetry_isolate.py` already established that
removing it makes notification **strictly less** reachable.

_Chosen:_ eleven tests that state each property in words, so whoever
changes `combine()` has to change a test saying which property they are
giving up.

_Rejected:_ changing the algorithm. CLAUDE.md is right that this needs
a decision rather than a patch, and a decision made by whoever happened
to be editing the file is not one.

### A signal scoring 0.0 was telling the user it had matched

`score_title` returned the reason `"title overlaps a target role"` on
every scoring path, including when the computed overlap was empty. So a
stored recommendation could read:

```text
title_score = 0.0    match_reasons = [..., "title overlaps a target role", ...]
```

Found on a real row during the Day 12 end-to-end run: "NLP Engineer"
against `["AI Engineer", "ML Engineer", "Machine Learning Engineer"]`.
Every shared word is a weak token, so the real overlap is empty and the
score is correctly 0.0 — the **number** was right and the **sentence
next to it** was not.

This is worse than an internal mislabel. `match_reasons` is the "match
explanation" shown to a user, so the system was making a claim to a
person that its own data contradicted. It is invisible unless somebody
reads the reason and the score in the same query, which is how it
surfaced.

_Chosen:_ branch the reason on the value. Only the string changes.

_Rejected:_ making a 0.0 title score abstain. "We compared and they do
not match" is a real answer, and turning it into an abstain would
change `weight_covered`, which changes the notification gate — a model
change dressed as a text fix.

`score_skill` and `score_location` were checked for the same shape and
are correct: "0 of 3 required skills" and "different city" both explain
a zero honestly. `test_no_signal_claims_a_match_while_scoring_zero`
now checks all three together.
