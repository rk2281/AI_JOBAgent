# CLAUDE.md

Persistent instructions for any coding agent working in this
repository. It lives at the repo root, where Claude Code loads it
automatically at the start of every session; nothing needs to be
pasted.

If you are using a tool that reads `AGENTS.md` instead, copy this file
to that name as well. Keep one of them a copy of the other rather than
maintaining two.

---

## 0. The rule that outranks the others

**A success status is not success.**

When you add a status, a count, or a score, state what it would look
like if the work silently did nothing. If you cannot tell those two
cases apart, the thing you added is not a check.

This project has already been bitten by this. A scoring run reported
`complete_no_qualifying` — a status explicitly documented as healthy —
while 40% of the model had never contributed a single value.

---

## 1. Do not "fix" these

These look like bugs. They are decisions. Changing any of them without
being explicitly asked is the single most damaging thing an agent can
do in this repository, because none of these produce a failure when
"fixed" — they produce a plausible-looking success.

| Observation                                                                                                         | Looks like                                                          | Actually                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| ------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `abstain_experience = 98 / 98`                                                                                      | extraction is broken                                                | source data ceiling — descriptions truncated at Adzuna's 500-char cap; only 37 of 99 mention years at all                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `notify_eligible = 0`                                                                                               | the gate is broken                                                  | the gate is working: `weight_covered` 0.50 < `min_weight_covered_to_notify` 0.55                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| signal columns on `recommendations` are NULL                                                                        | missing data, default them to `0.0`                                 | **NULL _is_ abstain.** Defaulting to 0.0 destroys the entire abstain model. A test exists to keep them nullable — do not relax it                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `PARTIAL` / `FAILED` never appear in `scoring_runs`                                                                 | dead enum members, delete them                                      | nothing sets them yet                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `jobs_remote` and `jobs_hybrid` both 0                                                                              | counter is broken                                                   | `work_mode` is NULL on 94 of 99 jobs                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| company matching is exact, not substring                                                                            | the `\|\|` multi-company field is being missed                      | deliberate — see Day 8 record §3.2                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `list_needing_enrichment()` does not filter `is_excluded`                                                           | wasted API quota on job 2                                           | deliberate — having skills and being scorable are different questions (§8.4)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| dry-run prints `missing skills 91` and `would enrich 97`                                                            | the numbers should agree                                            | they count different things; both correct (§8.5)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| both notification branches point at `finalise`                                                                      | a stub                                                              | deliberate — the routing rule is real and tested in both directions; Day 11 changes one entry of `NOTIFICATION_PATH_MAP`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `stages_persisted` is `(none)` on a dry run                                                                         | persistence is broken                                               | dry-run scoring and dry-run enrichment write nothing by design; `computation_performed` is the field that says work happened                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `jobs_enriched` prints `None` after a dry run                                                                       | should be `0`                                                       | the dry-run path returns before computing it — **absent, not zero**, same distinction as the NULL signal columns                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| coverage floors 0.45 and 0.40 give the same result as 0.50                                                          | the grid is broken                                                  | `weight_covered` has three observed values, so the floor is a step function, not a dial (Day 9 note §9.2)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `app/workflows/` imports no repository                                                                              | inconsistent with services                                          | deliberate — `resolve_targets` calls a service so the rule needs no exception, and an exception is a hole to grow into                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `users_skipped_no_cv` counts three different things                                                                 | the name is wrong, rename it                                        | deliberate — renaming breaks comparison against every `scoring_runs` row written before the breakdown existed. `users_skipped_no_profile` / `_no_active_cv` / `_cv_not_embedded` say which cause applied; only the third is fixable by running the embedding pass                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| an `agent_runs` row with `finished_at` NULL and every counter NULL                                                  | persistence is broken                                               | deliberate — the row is opened BEFORE the graph runs and completed after, so a run killed mid-flight leaves evidence rather than nothing. `ix_agent_runs_unfinished` indexes exactly that predicate. `scoring_runs` uses the same shape (`status='running'`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| every `notifications_*` column NULL on an `agent_runs` row                                                          | delivery persistence is broken                                      | the notify branch never executed. `notify_eligible` 0 routes to `no_qualifying`, which goes straight to `finalise`. **Absent, not zero** — a run that never delivered has no opinion about how many messages it sent. A delivery that ran and found nothing reports `notification_status = complete_no_qualifying` with `notifications_eligible_selected = 0`; those two states are distinguishable on purpose                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| the partial index predicate says `WHERE status = 'SENT'`, uppercase                                                 | a typo — the enum value is `"sent"`                                 | SQLAlchemy persists an enum by its **NAME**, so the PostgreSQL labels are `PENDING`/`SENT`/`FAILED`. Verified against `pg_enum`. Lowercase with a `::text` cast would be created successfully and match **nothing, forever** — duplicate prevention absent while the migration reports success. `scripts/notification_constraints_check.py` proves the index FIRES rather than that it exists                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `notifications` holds several rows for one `(user_id, job_id)`                                                      | the unique constraint was lost                                      | deliberate — Day 11 made it an ATTEMPT table. The old `uq_notification_user_job` made a _failure_ permanent, locking a user out of a job after one outage. At most one `SENT` row per pair; any number of `pending`/`failed`. No attempt ceiling, also deliberate                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| a notification message omits Company, Location or Experience                                                        | the formatter is dropping fields                                    | the column is NULL and a missing line is the correct rendering. `jobs_with_experience_bounds` is 0 and `work_mode` is NULL on 94 of 99                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `--top`/`--bottom` print a full ranked list under a `no_scorable_users` status                                      | the funnel is lying, or scoring silently worked                     | both are correct. `--top`/`--bottom` read `recommendations` directly and never recompute (the script's own docstring says so), so they print whatever a PREVIOUS run stored — possibly against a different CV version. Observed 2026-09-05: `pairs_scored 0` printed directly above a top row of 0.983 left over from an earlier CV                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| two `python.exe` processes per `run.py` launch                                                                      | two servers racing for one `getUpdates` slot                        | normal. `.venv\Scripts\python.exe` is the `py.exe` LAUNCHER, not a copy of the interpreter — its version info reads `InternalName: Python Launcher`. It re-execs `C:\Python312\python.exe` as a child and supervises. Verified 2026-09-05 with a script containing no imports at all: still two processes. Only one binds port 8000 and only one line of `Application started` appears. Check the startup log for a bind error before concluding anything from a process count. **Separately, unresolved:** a real silent-failure incident on 2026-09-05 (two `/preferences` taps at 17:22/17:24 got no reply, `/help` didn't show a just-added command) was initially, wrongly, attributed to this two-process shape. It is NOT explained by it. The leading hypothesis — a process still running from before the 17:12–17:15 code fix was still serving Telegram at 17:22/17:24 — is plausible and **unproven**; the process in question was gone before it could be inspected. Recorded honestly as unexplained rather than closed with the wrong answer. See "Open" below for the operational gap this exposed.                                                                                                                                                                                                                                                               |
| the nightly scheduled task registered fine but `LastTaskResult` came back `3221225786` on its first unattended fire | a bad command line or a bad path, same family as the two rows above | `Register-ScheduledTask` with no `-Principal` silently defaults to `LogonType Interactive` — verified 2026-09-05 by reading the LIVE registered task (`(Get-ScheduledTask ...).Principal`), not the script, which never claimed otherwise. Interactive means the task body only runs while that user has an interactive logon session open — a different contract from "runs unattended overnight," and `3221225786` (`0xC000013A`) is consistent with the process being torn down for want of one. `scripts/schedule_agent.ps1` now builds an explicit `New-ScheduledTaskPrincipal -LogonType S4U -RunLevel Limited`, which is what an unattended per-user task needs — it runs without a stored password and without requiring the interactive session `Interactive` silently assumed. **Re-registering with S4U requires an elevated session** (`Register-ScheduledTask` returned `Access is denied`, `HRESULT 0x80070005`, from a non-admin shell with no interactive desktop to answer a UAC prompt), so as of this writing the fix is written and self-tested but not armed — the old `Interactive` task is still the one registered. Do not treat the S4U change as proven until a real unattended fire has been checked against `LastTaskResult`, the newest `logs\agent_*.log`, and a new `agent_runs` row — the same three-way check this row itself was verified with. |

**This list is a reconstruction and is known to be incomplete.** The
original list lived in `prompts/day8_open_issues.md`, which was absent
from the archive it was reconstructed from. If that file resurfaces,
reconcile against it. Until then, treat anything in `docs/` described
as "deliberate", "on purpose", or "not a bug" as belonging on this
list even if it is not written above.

---

## 2. Working method

- **Explain the decision before writing the code.** Options considered,
  what was rejected, why. Working code that the author does not
  understand is worth little here.
- **Read the code a change will touch before proposing the change.**
- **Never report a total you computed.** Report before and after as two
  separate numbers and let the human compare. Agent arithmetic in this
  project has been wrong four times.
- **When a failure has several plausible causes, build the thing that
  separates them** instead of guessing the next fix.
- **A cheap check before an expensive one.** A regex over stored text
  costs nothing and can predict what a day of API quota would prove.
- **Write the prediction down before the run.** That is what makes a
  surprise legible as a surprise instead of as noise.
- **Two explanations for one observation means the checking is not
  finished.** This applies to documents as much as to data.
- **A threshold written with `<` misses the boundary.** Always ask what
  happens exactly at it. Every notification gate here uses `>=` and is
  tested at exactly its floor.
- **An assertion firing against your own model is the assertion
  working.** Suspect the model before the data.
- **A funnel that balances may be checking the plan, not the work.**
  Ask which stage a check actually observes.
- **Invisible is worse than wrong.** Anything silently skipped or
  excluded must produce a number a human can read.

---

## 3. Secrets

- **Never read, print, echo, `cat`, or `grep` `.env`.** Five
  credentials live there.
- Nine leak incidents so far. **None came from printing `.env`** — every
  one came from something handling a secret incidentally. Assume the
  next one will also not look like a secret operation.
- The `httpx` INFO-logging bug is the canonical example: Adzuna's
  `app_id` and `app_key` are **query parameters**, so logging request
  URLs printed both on every ingestion run, for months.
- **Before adding any logging of a URL, request, or exception from
  `app/integrations/`, check whether credentials travel in the query
  string.**

### Archives

Never hand-make a zip of this repository. Use:

```powershell
powershell -File scripts/pack.ps1 -SelfTest   # first, once
powershell -File scripts/pack.ps1
```

`pack.ps1` builds from `git archive`, so `.env`, `storage/`, `.git/`,
`*.zip` and `*.pdf` are excluded by construction, and it asserts its own
output before handing you the file. Every leak so far came from
Explorer's "Compress to zip" or `Compress-Archive -Path .`, neither of
which has ever heard of `.gitignore`.

**A valid archive can still be a useless one.** Untracked project
instructions and records are invisible to `git archive`; therefore a
self-testing pack script can produce a structurally valid archive that
silently loses the context required to operate the project. `pack.ps1`
asserts what it excluded, not what it should have included -- it cannot
assert what was never committed. Before packing, run `git status
--short --untracked-files=all` and decide about every `??` line rather
than letting it default to absent.

---

## 4. Layering

- No SQL in handlers.
- No Telegram imports in services.
- No business logic in repositories.
- `app/integrations/` is the only place a third-party **service** SDK is
  imported (Adzuna, Gemini). This rule is about vendor network clients,
  not about every third-party package — `sqlalchemy` lives in
  repositories, `pydantic` in schemas.

Entry points follow one shape: a module-level `async def run_x(...)`
that owns its own transactions, commits per unit rather than once at
the end, and returns a dict of counters. `run_ingestion`,
`run_enrichment`, `run_job_embedding`, `run_scoring`. Follow it.

---

## 5. Environment

- Windows / PowerShell.
- Scripts run as `python -m scripts.name`, never `python scripts/name.py`.
- **`pytest-asyncio` is NOT installed and must not be installed.** Async
  tests are plain synchronous functions driving a coroutine with
  `asyncio.run()`. See `tests/test_job_ingestion.py`.
- **`python -c` sometimes prints nothing in this shell, with no error.**
  Cause unknown. Use a file.
- **Anything containing quotes or JSON goes in a file, not on the
  command line.** A JSON literal passed through PowerShell quoting
  reached Postgres with its quotes stripped and its backslashes intact.
- Long terminal output gets truncated when pasted. If a query would
  return more than a few rows, write a narrower query.

---

## 6. Where things stand

|                             |                                                                                                                                                                                                                                                                                                                                                                                     |
| --------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Alembic head                | `d30da11e80fa` — 12 migrations; `alembic check` clean as of Day 16 (verified with `alembic heads`)                                                                                                                                                                                                                                                                                  |
| Tests                       | 786 collected; **745 passed + 41 skipped without a database** (directly measured, Day 16); **with a database, last directly measured at 785 passed, 0 failed** — that figure predates this Day's one new test (`tests/test_instant_recommendation_embed_logging.py`) and was not rerun as a combined suite afterward, only the 41-test integration subset was (41 passed, 0 failed) |
| Workflow                    | `app/workflows/` — 8 nodes, 3 conditional edges; runs persisted to `agent_runs`                                                                                                                                                                                                                                                                                                     |
| Jobs                        | 695 total (689 real + 6 `synthetic_test`, verified live Day 16), all embedded, 1 excluded (job 2)                                                                                                                                                                                                                                                                                   |
| CV versions                 | 3 active, all embedded                                                                                                                                                                                                                                                                                                                                                              |
| Enriched jobs               | 130 with `skills_extracted_at` not null, of which 74 produced at least one skill (measured live 2026-09-23, Day 16b; previously recorded as "5, of which 2" — stale, not a regression, just never updated as enrichment kept running)                                                                                                                                               |
| Jobs with experience bounds | 0                                                                                                                                                                                                                                                                                                                                                                                   |
| Active scoring signals      | **3 of 5**                                                                                                                                                                                                                                                                                                                                                                          |
| Notifications sent          | includes real `trigger_source = 'preferences'` and `'onboarding'` deliveries as of Day 16, in addition to `manual_test` — **the scheduled nightly gate itself has still sent 0**                                                                                                                                                                                                    |
| Feedback rows               | 0                                                                                                                                                                                                                                                                                                                                                                                   |

Weights (`Suggested Weight` column of the plan spreadsheet's
"Matching & Scoring" tab, and matching the code exactly): skill 30%,
semantic 20%, experience 20%, location 15%, title 15%. A validator in
`app/core/config.py` refuses to construct `Settings` if they do not sum
to 1.0. Changing any weight requires bumping `weights_version`.

### Read before changing anything in scoring

- `docs/Day_8_progress.md` — titled "Day 8 — Matching and Scoring".
  Parts 7 and 8 are the ones that matter to new work.
- `docs/CODEBASE_GUIDE.md`

---

## 7. Open, and not to be tidied away

- **Adzuna credentials have not been rotated** after the `httpx`
  logging exposure. Rotate Adzuna before Gemini — Gemini's key travels
  in a header and was never printed.
- **Job 81 is permanently locked out.** `skills_extraction_attempts` is
  at the ceiling of 3. Only `--retry-failed` reaches it. Seven more
  jobs sit at 1 attempt from old 429s.
- **Gemini free tier is daily** and currently grants roughly one call
  before 429. A full enrichment pass is ~97 calls, 27–84 minutes.
- **Prediction on record:** after a full enrichment pass,
  `abstain_skill` should fall sharply and `abstain_experience` should
  fall only to roughly **60**, not 0. If it lands near 0, something is
  inventing values that were not in the description text.
- **Abstention is mildly rewarded rather than neutral.** A signal that
  abstains leaves the denominator; a signal scoring 0.0 stays in it. So
  a job with _missing_ data can outrank a job with _bad_ data. Observed
  on real data and verified by hand. This is abstention applied
  consistently — but nobody has decided it. **It needs a decision, not
  a patch.**
- **One company field holds three companies joined by `||`.** Exact
  match will never catch it. Undecided.
- **The agency list may be incomplete** — 6 more candidates found, which
  would take affected pairs from 29 to 35. Undecided.
- **`--top` prints the `title` header twice.** Cosmetic.

### Day 9 decisions, settled

1. **`langgraph` lives in `app/workflows/`**, imported by exactly one
   module (`graph.py`), asserted by a test. Not `app/integrations/` —
   that directory is for anything making a network call on someone
   else's credentials, which langgraph does not. Not `app/agent/` —
   `CODEBASE_GUIDE.md` already reserved `app/workflows/` for scheduled
   matching runs.
2. **`embed_jobs` is a node**, though absent from the plan's Day 9 row.
   A test walks every path from `discover_jobs` to `score_and_rank` and
   fails if any misses it. Plan gap, not a code gap.
3. **No `agent_runs` table.** `build_run_summary(state) -> dict` is the
   seam: pure, separately tested, returning the exact fields the row
   would hold. Day 10 persists a dict that already exists.
4. **The probe was built and run.** Prediction (1–4) was correct: 2.
   See `docs/Day_9_Design_Note.md` §9 — and do not change a threshold
   on the strength of it.

See `docs/Day_9_Design_Note.md` and `docs/Day_9_progress.md`.

### Open after Day 9

- **`users_skipped_no_cv` conflates two states.** "No active CV version"
  and "active version not embedded" are indistinguishable to every
  caller, and only the second is fixable by running the embedding pass.
  Pre-existing. Deliberately not fixed inside the graph — that would
  make the graph's definition of scorable differ from scoring's.
  `scripts/scorable_targets_check.py` prints all four states, which is
  currently the only place the distinction is visible. **Needs a
  decision.**
- **`httpcore2`, `httpx2` and `truststore`** were written into
  `requirements.txt` for the first time on Day 9. They were already
  installed and are not Day 9 additions, but nothing has verified them.
- **`langsmith` ships with `langchain-core`.** It is a telemetry client
  that activates on `LANGCHAIN_TRACING_V2` / `LANGSMITH_*` environment
  variables. Nothing in this repository sets or reads them — verified by
  searching source, not by reading `.env`. Confirm none is set in the
  deployment environment before Day 10 runs the graph unattended: an
  enabled tracer would ship graph state to a third party.

### Closed by Day 10 Part 1

All three "Open after Day 9" items above are closed. The settled
decisions, because each has a live alternative somebody will propose:

- **`is_scorable_user` was NOT split**, and must not be. Its docstring
  says so and the docstring is right: the gate stays a function of
  exactly two booleans, and `classify_skip_reason()` is reporting
  layered on top, reached only after the gate has returned `False`. That
  ordering is what makes the breakdown structurally incapable of
  changing who gets scored. It asserts rather than returning a fallback
  when handed a scorable user — a plausible string there would let the
  counters sum and the funnel balance while a scored user was reported
  as skipped.
- **`users_skipped_no_cv` keeps its name.** It counts three things and
  is literally accurate for only one of them, and it stays anyway:
  renaming it would make every `scoring_runs` row written before the
  breakdown unreadable against every row after. A column meaning one
  thing up to a date and another afterwards is worse than a column with
  an imprecise name. The three new counters say which cause applied.
- **The aggregate query in `scripts/scorable_targets_check.py` was
  rejected** as the source of the breakdown, though it is cheaper. It
  observes the PLAN — one count read around the loop — where the loop
  observes the WORK, and reusing it would make that script share code
  with the thing it cross-checks, which
  `test_scorable_targets_check_does_not_reference_the_shared_predicate`
  exists to prevent. The breakdown costs one extra query on the skipped
  branch only: a run that skips nobody pays nothing.
- **`httpcore2`, `httpx2` and `truststore` are transitive, not loose
  pins**, and are not removable while `langgraph` is a dependency:
  `langgraph -> langchain-core -> langsmith -> httpx2 -> httpcore2 /
truststore`. Verified with `pip show`; the chain is recorded above the
  pin in `requirements.txt`. `httpx2` is present BECAUSE the telemetry
  client is, so this was one issue with the item below, not two.
- **Tracing fails closed.** `assert_tracing_disabled()` is the first
  statement of `build_graph()`. It raises rather than warns, because a
  warning about telemetry is read after the run that already sent the
  data, and it reports variable NAMES only — two of them are
  credentials. Both spellings are checked, since `langchain-core`
  renamed `LANGCHAIN_*` to `LANGSMITH_*` and still honours the old ones.
  Flags and credentials are read differently — see the Part 2 entry
  below, which corrects what this bullet originally said.

### Open after Day 10 Part 1

- **The `.env` leak is not remediated.** `.env` was present in a shared
  archive, so `GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `ADZUNA_APP_KEY`
  and `DATABASE_URL` have left the machine. Rotation is a human task and
  has not been done. Incident ten, and like the nine before it, it did
  not come from printing `.env`. `scripts/pack.ps1` needs no change —
  its `$ForbiddenPatterns` already covers `.env` and fails closed; the
  archive was not built with it.
- **The skip breakdown is not in the workflow summary.**
  `build_run_summary()` reports `users_scored` but not the three skip
  counters, so a scheduled Day 10 run logs the total without the cause.
  `scripts/score_jobs.py` prints them; `scripts/run_agent.py` does not.

### Closed by Day 10 Part 2

- **Tracing flags and tracing credentials are read differently, and the
  reason is not style.** A FLAG carries a value that means something:
  `false` is the documented way to turn tracing off and it is what
  appears in Compose files, CI configs and deployment templates written
  by people being careful. The first version of this check treated any
  non-empty value as enabled, so it fired hardest on the environment it
  existed to bless — the process died claiming tracing was on, in front
  of a config line saying it was off. **A guard that is wrong in exactly
  the case it approves gets deleted by the next person under time
  pressure, and then there is no guard at all.** Flags
  (`LANGCHAIN_TRACING`, `LANGCHAIN_TRACING_V2`, `LANGSMITH_TRACING`) are
  now parsed for truthiness, case-insensitive and stripped. Credentials
  and destinations are unchanged: presence alone is the signal, because
  no value makes `LANGSMITH_API_KEY` innocent in an environment that is
  not tracing.
- **An unrecognised flag value counts as ENABLED.** Only the disabled
  values are enumerated in `config.py`, so the fail-closed direction is
  structural rather than accidental — listing the enabled ones instead
  would make an unknown value fall through to "not enabled" by accident.
  A false positive costs a crash naming a variable; a false negative
  costs CV text exported with no signal.
- **The flag branch reads values and must never emit one.** It is the
  only place in `config.py` that looks at a value; the value is consumed
  by `_flag_is_enabled()` and only the NAME is returned. Tested.
- **The skip breakdown reaches `build_run_summary()`.** No plumbing
  change was needed: `score_and_rank` already stores `run_scoring`'s
  whole dict as `state["scoring"]`. Absent stays `None`, never `0` —
  a scoring stage that never ran has no opinion about who was skipped.

### Open after Day 10 Part 2

- **The `.env` leak is still not remediated.** Human task, still has a
  clock on it.
- **The workflow skip breakdown cannot be forced live.** `--user-id
9999` exercises it through `scripts/score_jobs.py`, but not through
  `scripts/run_agent.py`: `resolve_targets` reports
  `users_with_embedded_cv 0` and routing stops the run at `finalise`
  before scoring executes. That is the graph working as designed. It
  means the breakdown's end-to-end path is covered by a stubbed test
  only, and will stay that way until a real user is unscorable while
  another is not.

### Closed by Day 10 Part 3

- **`agent_runs` exists.** 38 columns derived from `build_run_summary()`
  plus `id`; the Day 9 claim that the summary would still be the schema
  on Day 10 held exactly. `test_every_summary_key_has_a_column` fails if
  a summary key has nowhere to land — Day 11 adds fields, and a field
  with no column is silently dropped on write while every test stays
  green.
- **Two writes, not one.** The row is opened before the graph runs and
  completed after, so an interrupted run leaves `finished_at IS NULL`
  rather than no row. Not a status enum — that is Day 11. This is now a
  §1 row, because it will look like broken persistence.
- **Persistence lives in `scripts/run_agent.py`, never in a node.**
  Forced by the §1 row that `app/workflows/` imports no repository. The
  driver owns the database; the graph owns the decisions.
- **The nightly task is registered by `scripts/schedule_agent.ps1`**
  (`-SelfTest` arms nothing; it passed). Absolute venv interpreter,
  explicit working directory, exit code written as the last line of a
  gitignored log. No `--dry-run`, ever.

### Open after Day 10 Part 3

- **CREDENTIAL ROTATION IS STILL NOT DONE, and this is now incident
  eleven.** A shared archive not built with `pack.ps1` contained `.env`,
  the whole of `storage/cvs/` — real candidate CV PDFs for users 2, 3
  and 10 — and a stray `files (1).zip`. Three of the five
  `$ForbiddenPatterns`, in one archive. The four credentials have now
  left the machine **twice** and have never been rotated.
  `storage/` is a category no previous incident involved: a key can be
  replaced, a leaked CV cannot be recalled. `pack.ps1` needs no change;
  `-SelfTest` passes, 145 files. **It was bypassed, not broken.**
- **Nothing writes `state["errors"]`, so the graph's `failed` status is
  unreachable.** `run_agent.py` exits non-zero on `failed` or
  `degraded`; only `degraded` can actually occur. Same shape as
  `PARTIAL`/`FAILED` never appearing in `scoring_runs`. Do not invent a
  writer to make it reachable; do not rely on it either.
- **`scoring_runs` has no columns for the skip breakdown.** Part 1 added
  the three counters to the dict `run_scoring` persists, and
  `ScoringRunRepository.finish()` compiles that dict straight into
  `values(**counters)`, so the first non-dry run raised `CompileError:
Unconsumed column names`. It survived two parts and a commit because
  the suite has no database and every live check had been `--dry-run`.
  Fixed by removing the three keys from the persisted dict; they are
  persisted in `agent_runs` instead. **Adding the columns to
  `scoring_runs` remains undecided** — it would be a second copy of
  numbers `agent_runs` already holds, in a table whose funnel assertion
  does not cover them.
- **`scripts/concurrent_claim_dryrun.py` is not a dry run.** It fires
  real Gemini extractions. Running it spent two calls and left **CV 19
  in `extraction_status = 'extracting'`**, which may cause future
  extraction claims to skip it. Not repaired — it is a data mutation
  nobody authorised and it is unclear whether the row was already stuck.
  In this repository "dryrun" means "no writes" for `scoring_isolate`
  and `notify_reachability_probe`, and means something else here.
- **The scheduled task has never been triggered by Windows.** §2.4 was
  blocked by the rotation above. `-SelfTest` proves the interpreter, the
  working directory, the log path and the command line; it does not
  prove Windows starts the task, that its credentials can read the repo,
  or that a log lands with a real exit code in it.
- **Named, not built: a pre-share archive verifier.** Both archive
  incidents were a person reaching for Explorer or `Compress-Archive`
  instead of the script, and nothing in the repository can intercept a
  right-click elsewhere. `pack.ps1` guarantees only its own output,
  which does nothing about an archive it did not produce. The cheapest
  loud failure is `scripts/verify_archive.ps1 <path>`, applying
  `$ForbiddenPatterns` to an arbitrary zip before it is shared.
- **The abstention asymmetry is measured, not decided.**
  `scripts/asymmetry_isolate.py` (read-only, `combine()` not imported,
  self-checking against the stored `final_score` to 1.1e-16). The
  finding that matters: **removing the asymmetry makes notification
  strictly less reachable.** Candidate B — abstentions kept in the
  denominator — yields `notify_eligible = 0` at every floor down to
  0.30, because it halves the score range (max 0.9835 → 0.4917) against
  an unmoved 0.7 threshold. The asymmetry is currently the only reason
  any pair is near the gate. Day 11 decides; Day 10 does not.
- **`users_skipped_no_profile` can never be non-zero through
  `run_agent.py`.** `select_target_user_ids` draws ids from
  `Profile.user_id`, so every id in the loop has a profile by
  construction, and `--user-id X` for an X with no profile terminates at
  `no_scorable_users` before scoring runs. A zero there is evidence of
  nothing. Documented where the counter is defined.

### Open after Day 10 Part 4

- **Notification is reachable today by one config change, and that is a
  decision nobody has made.** `min_weight_covered_to_notify` 0.55 → 0.50
  takes `notify_eligible` from 0 to 2. 242 of 294 pairs sit at
  `weight_covered` exactly 0.50, five hundredths under the gate. The two
  §1 rows about this — "the floor is a step function, not a dial" and
  "`notify_eligible = 0` … the gate is working" — are both **correct**
  and neither is being challenged. What is wrong is the operational
  conclusion drawn from them, that coverage is a dead end. **Do not
  change the floor to make this go away.** See the Day 10 Part 3 record,
  Task 3, first section.
- **The two qualifying pairs are both user 2's, and both score high for
  a structural reason rather than a match reason.** Where skill and
  experience abstain and location and title are both 1.0, the
  renormalisation reduces exactly to `final = 0.4 * semantic + 0.6` —
  verified against stored values at difference 0.000e+00. So
  `final_score` has a **floor of 0.60** for that shape however poor the
  semantic match, and clears the 0.7 threshold at `semantic >= 0.25`.
  This is the abstention asymmetry as a closed form. It means the floor
  and the threshold cannot be decided separately: lowering the floor
  admits precisely the population the floor guards against.
- **`concurrent_claim_dryrun.py` should be renamed to
  `concurrent_claim_probe.py`.** It fires real Gemini extractions. Not a
  §1 row — §1 is for things that look like bugs and are decisions, and
  this is a defect with a victim row; a §1 entry would immunise it
  against being fixed. The rule the name breaks is worth stating
  outright: **"dryrun" in a script name is a promise of no writes.**
  **Superseded 2026-09-18 — done. See the Day 14 entry.**
- **CV 24 — user 2's active CV — reads `extraction_status = 'failed'`
  while its extracted version exists and is embedded.** The script
  re-extracted an already-complete CV on 2026-09-04, the call timed out,
  and the status was overwritten. No data was lost (`cv_versions` id 10
  is intact), and nothing downstream noticed because scoring gates on
  `cv_versions.embedding`. `profile_view.py` is the one consumer that
  does read it, i.e. what the user sees. Needs a decision, not a repair.
- **CV 19 is NOT locked out and needs no repair.** Its claim is ~5 days
  old against a `DEFAULT_STALE_AFTER` of 15 minutes, so
  `claim_for_extraction()` will take it. The Part 3 record's worry was
  wrong, and so was its attribution: CV 19 has not been written since
  2026-08-30 and the script never touched it. Proposed instead, not
  built: a read-only check printing every `extracting` row with its claim
  age, so a claim _younger_ than the window whose process died becomes
  visible — that case is currently invisible.
- **`docs/Day_6_JobIngestion.md:786` says "about ten" Adzuna calls on
  ingestion runs; the table says 7** and the table is right.
  `run_ingestion` opens its row before any network call, so no run that
  spent a call is missing. Correct the prose in Day 11. Probe calls
  remain recorded nowhere, and a _failed_ call is spent without
  incrementing `pages_fetched` — the ingestion-run spend is exact, the
  total is not.
- **Adzuna credential rotation now blocks knowing the quota, not just
  spending it.** The only authority on what remains is Adzuna's own
  dashboard, which needs the credentials that have leaked twice.
- **The Day 10 prompts are now committed, unfolded and self-contradicting
  on purpose.** They reached the repository after the work, not before.
  `day10_part3.md` §2.2 says Windows Task Scheduler; the amendment's §C
  replaces it and says argue the choice. Amendment §A asked for the fold
  to happen _before_ any code — that precondition is gone, and folding
  now would overwrite the instruction the delivered code was written
  against. **Whether to fold them is a human decision, not an agent
  tidy-up.**
- **Divergence from the plan was pre-authorised; diverging silently was
  not.** Amendment §B: "Divergence is allowed here. What is not allowed
  is diverging without saying so." Task Scheduler was a permitted choice.
  The failure was that the argument was written five days late instead of
  before the code, which left a defensible decision looking identical to
  an unexamined one.
- **Nothing observes whether the nightly run happened.** A skipped night
  writes no log and leaves no `agent_runs` row, and nothing looks for
  either absence — §0's shape exactly. Task Scheduler survives reboots
  and exposes `LastTaskResult`, which is more survivable than APScheduler
  but is still not observed by anything here. **Named, not built:** a
  staleness check over `agent_runs.started_at` that complains when the
  newest row is older than about 26 hours. One query, mechanism-independent,
  and it observes the work rather than the plan.

---

## 8. Prompts and staging

Multi-step work is written as staged prompts with hard rules at the
top, exact file paths, exact find/replace strings, a table of expected
test values, and explicit verification commands. **Stop and report
between stages.** Do not run stage 3 because stage 2 looked fine.

---

## 9. Day 12 — completion, verification and finalisation

Day 12 was an audit-and-verify phase, not a feature phase. No feature
was added. What changed is that claims which had never been executed
are now executed, and three defects that every test had been passing
over are fixed.

### The one that matters most

**A shared archive leaked `.env`, `storage/cvs/` (21 real candidate CV
PDFs) and `logs/` for the THIRD time.** `pack.ps1` was not broken; it
was bypassed again. The four credentials have now left the machine
three times and have never been rotated. Rotate Adzuna first — its
credentials travel in a query string. A key can be rotated; a CV
cannot be recalled.

`scripts/verify_archive.py` and its `.ps1` wrapper now exist, which is
the "named, not built" item from the Day 10 Part 3 record. Pointed at
the offending archive it exits 1 and names every entry.

### Closed by Day 12

- **The ORM metadata had drifted four objects from the migrated
  schema, and `alembic revision --autogenerate` would have dropped
  them.** `alembic check` had never been run. It proposed removing
  `uq_job_content_hash`, `ix_jobs_embedding_hnsw`,
  `ix_cv_versions_embedding_hnsw` and `ix_agent_runs_unfinished`.
  Dropping the first removes ingestion's duplicate defence with no test
  failing, because the suite had no database. All four are now declared
  in `__table_args__` — no DDL change, the objects already exist — and
  `alembic check` reports no operations. **`content_hash` is no longer
  `index=True` on the model**; migration `d7a3f1c92b40` replaced that
  index with a unique constraint on Day 6 and the model was never
  updated.
- **The test suite was polling the production Telegram bot.**
  `tests/test_health.py` ran the real lifespan, which found
  `TELEGRAM_MODE=polling` and a real token and called `initialize()`
  and `start_polling()`. Telegram allows one `getUpdates` consumer per
  bot, so `pytest` raced the real bot and any update the test process
  won was acknowledged and gone. All three tests passed throughout.
  Fixed structurally by `tests/conftest.py`, which pins a hermetic
  environment before any `app.*` import. Do NOT replace this with a
  pytest check inside `lifespan`: that makes the tested startup path
  differ from the shipped one.
- **A signal scoring 0.0 was claiming a match.** `score_title` returned
  "title overlaps a target role" unconditionally, so a stored row could
  read `title_score = 0.0` beside a reason asserting a match.
  `match_reasons` is user-facing, so that was a false claim made to a
  person. The reason now branches on the value. The value, the weight
  and every gate are unchanged — turning a 0.0 into an abstain would
  have been a model change dressed as a text fix.
- **An unrecognised `TELEGRAM_MODE` is now fatal.** `poling` used to
  start the API cleanly, serve `/health` as `ok`, report
  `telegram.configured: true` and never answer a message. `webhook` is
  rejected by name because no webhook route exists anywhere.
- **Integration tests exist — 23 of them, against a real PostgreSQL.**
  They cover all five pipelines and execute the two failure shapes this
  project has actually paid for: a summary key with no column, and a
  counters dict compiled into an UPDATE. Migrations run through the
  alembic CLI in a subprocess, because that is the command a human
  runs.
- **The notify branch has executed.** Not on live data — on a fixture
  built to clear all three gates. It writes an attempt row, transitions
  it to `SENT`, and the second pass selects nothing.
- **`scripts/check_run_freshness.py`** answers "did the nightly run
  happen", by looking at `agent_runs` rather than at the scheduler.
  Three states, because a new deployment and a dead scheduler both have
  no recent run and only one is an incident. Window 26 hours, compared
  with `<=`, tested at exactly the boundary.
- **`scripts/e2e_verify.py`** drives one candidate through all 17
  stages and prints a trace labelling each REAL or STAND-IN.

### Do not "fix" these either — additions to section 1

| Observation                                                                                       | Looks like                        | Actually                                                                                                                                                                                                                                                                  |
| ------------------------------------------------------------------------------------------------- | --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `tests/conftest.py` sets environment variables at import, before any import of `app`              | import-order fragility to tidy up | load-bearing. pydantic-settings ranks OS environment above `.env`, and `Settings` is a cached singleton built on first import. Move these below an `app.*` import and the suite silently polls the production bot again. `tests/test_test_environment.py` is what notices |
| the environment guards live in `test_test_environment.py`, not in `conftest.py` where they belong | misplaced, move them              | pytest IMPORTS `conftest.py` but does not COLLECT tests from it. Written there they ran zero times while the suite reported clean — the exact mistake they exist to catch                                                                                                 |
| `Index("ix_jobs_embedding_hnsw", ...)` in a model, when a migration already creates it            | duplicated DDL, delete one        | the migration CREATES it; the model DECLARES it so autogenerate does not offer to drop it. Deleting either one has a different and worse failure                                                                                                                          |
| integration tests report `23 skipped` on a normal run                                             | tests quietly disabled            | they need `TEST_DATABASE_URL`. Nothing inside pytest can make an absent database loud, which is why `docs/TEST_RESULTS.md` records the with- and without-database figures separately                                                                                      |
| `test_logs_are_deliberately_absent_from_both_lists` asserts that `logs/` is NOT blocked           | an inverted test                  | it pins a known gap so that closing it is a deliberate change to three files rather than a silent drift. See "Open after Day 12"                                                                                                                                          |

### Open after Day 12

- **CREDENTIAL ROTATION IS STILL NOT DONE. Incident twelve.** Nothing
  in this repository can fix this.
- **`logs/` is gitignored but not forbidden.** Neither `pack.ps1` nor
  `verify_archive.py` blocks it, and the incident archive contained it.
  An unattended log is the one file nobody reviews before packing.
  **Needs a decision**, in three places at once.
- **The graph cannot run non-dry without Telegram.** The `notify` node
  builds a real `TelegramNotifier`; `deliver_notifications` accepts an
  injectable one but nothing threads it through. Not fixed —
  threading a test seam through the graph would change production
  wiring to suit a test.
- **Adzuna, Gemini and Telegram remain NOT VERIFIED end to end.** No
  egress during Day 12. Every rule around them is tested; no socket was
  opened.
- **The scheduler has still never been observed to fire.**
  `check_run_freshness` now makes a missed run visible, but nothing
  runs `check_run_freshness`. **Superseded 2026-09-06 — see the Day 13
  entry below.**
- **The abstention asymmetry is still measured and undecided.** Day 12
  added eleven tests stating each property in words and changed no
  arithmetic. `docs/MATCHING_AND_SCORING.md` holds the closed form.
- **Windows-specific paths were not exercised.** Day 12 verification
  ran on Linux, so `pack.ps1`, `schedule_agent.ps1` and
  `verify_archive.ps1` were not executed. The Python they call was.

---

## 10. Day 13 — the scheduler fired, and the enrichment backlog does not converge

### Closed by Day 13

- **Windows Task Scheduler has now actually fired the nightly task,
  twice, and it is verified — not inferred — from three independent
  places: `LastTaskResult`, a real `logs\agent_*.log` with more than a
  header, and a matching `agent_runs` row.** This closes the "never
  observed to fire" item above. Both firings (2026-09-05 20:18 IST /
  14:48 UTC, and 2026-09-06 10:03 IST / 04:34 UTC) ran the full 7-stage
  graph to completion: `resolve_targets`, `discover_jobs`, `embed_jobs`,
  `enrich_jobs`, `score_and_rank`, `decide_notification`, `finalise`.
  Both exited `degraded` (exit code 1) on `enrich_jobs: quota_exceeded`
  — that is the graph correctly reporting a real resource limit, not a
  crash. Both wrote a complete log and a complete `agent_runs` row.
  The `LogonType S4U` fix recorded in §1 above is what made this
  possible; it was armed by re-registering the task from an elevated
  session after the non-elevated agent session could get the code
  written and self-tested but could not register it itself (no admin
  token, no interactive desktop to answer UAC).
- **Embedding and enrichment are confirmed, from live `embedding_runs`
  rows rather than from the code comment alone, to be on separate
  quota buckets.** Across 2026-09-05 07:14 UTC through 2026-09-06 04:37
  UTC, `gemini-embedding-001` (`scope='jobs'`) completed three full
  100-job passes (runs 13, 17, 19), each ~13 api_calls, and never once
  returned `quota_exceeded` — including immediately after
  `gemini-3.6-flash` enrichment had just been refused. If the two
  models drew from one pool, embedding would have failed too. It did
  not, on any of the three occasions. This corroborates the existing
  `config.py` comment (Day 7 finding) rather than overturning it.

### Open after Day 13

- **The nightly enrichment backlog grows and does not converge, and
  this is a quota problem, not a code problem.** Ingestion inserts a
  fixed 100 jobs/night (`adzuna_max_pages_per_run=2` ×
  `adzuna_results_per_page=50`). `gemini-3.6-flash` (shared by
  enrichment and CV extraction) cleared 10, then 5, then 17, then 1
  job across four consecutive attempts on 2026-09-05–06, all within
  what looks like a single quota window — see below. `jobs_scored`
  went 195 → 294 → 394 over the runs recorded in this file; the
  enrichment-null count grew alongside it (179 → 263 → 363). At this
  ratio the backlog is monotonically increasing, not draining.
- **The exact shape of the `gemini-3.6-flash` quota is still not
  known, and CLAUDE.md's own "roughly one call before 429" (§7) looks
  like it was written on a day the quota was already mostly spent by
  earlier testing, not on a fresh day.** Timestamps converted to
  Pacific time (the timezone Google's free tier resets against) put
  ALL of runs 11–20 — from the 2026-09-05 00:14 PT early-morning manual
  testing through the 2026-09-05 21:37 PT "next morning" (IST) nightly
  run — inside the _same_ Pacific calendar day. Cumulative successful
  `gemini-3.6-flash` calls across that one Pacific day: 10 + 5 + 17 + 1
  = 33, hitting `quota_exceeded` three separate times (runs 16, 18, 20)
  at increasing cumulative totals. A hard once-a-day reset does not by
  itself explain three separate exhaustion events accumulating to 33
  successes in one day — this needs either a bigger daily ceiling than
  assumed, or a quota that is not a simple fixed-reset RPD. **Not
  resolved. Do not assume a specific number without checking Google's
  own quota console**, which needs the freshly-rotated Gemini
  credential and is a human task.
- **`adzuna_query_keywords` and `adzuna_query_locations` are still both
  empty** (confirmed live, not from memory), so every one of the 100
  jobs/night is drawn from the whole country, any domain. Of the 394
  currently-scored jobs, only 27 clear `semantic_notify_floor` (0.62)
  and only 19 clear `min_weight_covered_to_notify` (0.55) — roughly 5%
  of what ingestion brings in. The empty-keyword default is recorded
  in `config.py` as a deliberate, audited decision (see §1's guard
  against "fixing" it); this entry does not challenge that decision,
  only notes that it is the reason ingestion volume and relevance are
  so far apart. Changing it is a call for whoever owns the product
  decision, not something to flip unilaterally.
- **`run_nightly.ps1` runs the full pipeline every night — ingestion,
  embedding, enrichment, scoring — with no `--skip-ingestion`.**
  `scripts/run_agent.py` already accepts `--skip-ingestion` (and
  `--skip-embedding`, `--skip-enrichment`); nothing in the scheduled
  path uses it. Decoupling ingestion cadence from enrichment cadence
  (e.g., ingestion weekly, enrichment nightly) needs either a second
  scheduled task or a day-of-week branch in `run_nightly.ps1` — a
  scheduling change, not a code change, since the flag already exists
  and is already tested.

---

## 11. Day 14 — CV embedding automated, and a quota bucket nobody decided to share

### Closed by Day 14

- **CV embedding was never wired into the automated graph; now is.**
  `embed_cvs` is a new node in `app/workflows/graph.py`, added ahead
  of `resolve_targets` rather than after `discover_jobs` where
  `embed_jobs` sits — `resolve_targets`'s own gate reads
  `users_with_embedded_cv`, so a CV uploaded since the last run could
  never be counted by it unless something embeds that CV first, and
  nothing else in the graph did. Before this, a user who onboarded and
  uploaded a CV stayed permanently unscorable until a human ran
  `python -m scripts.embed_cvs` by hand — not skipped with a reason,
  never counted at all. This is the one place in the graph where "ask
  before spending" is deliberately not followed: `embed_cvs` runs
  unconditionally before `resolve_targets` can veto anything, because
  it is what produces the answer `resolve_targets` needs.
- **CV extraction and job enrichment were unknowingly sharing one
  free-tier Gemini quota bucket.** Both `GeminiClient` and
  `GeminiEnrichmentClient` read `settings.gemini_model` —
  `gemini-3.6-flash`, the same model, the same account-level
  free-tier ceiling. Not inferred: CV 33's extraction attempt failed
  at 2026-09-05 07:46:32 UTC with the identical 429
  (`generate_content_free_tier_requests, limit: 20, model:
gemini-3.6-flash`) that had already stopped an enrichment run
  eleven minutes earlier, after only 5 jobs. Fixed by giving
  extraction its own field, `cv_extraction_model`, defaulting to
  `gemini-3.1-flash-lite` — confirmed live reachable (a tiny
  no-schema prompt returned `status='completed'` in 6.0s) before
  being trusted, the same discipline `gemini_model`'s own comment
  already demanded of any replacement. `gemini_model` is now
  enrichment's alone.
- **`run_agent.py` never called `setup_logging()`.** Only
  `app/main.py` did. Every nightly `agent_*.log` therefore ran with
  no configured logging handler at all — Python's default "handler of
  last resort" surfaces WARNING and above only, so every per-job
  `logger.info(...)` line in enrichment and embedding
  (`"job N: X.Xs (ok)"` etc.) was silently dropped in every scheduled
  run to date. Confirmed by grepping 10 real logs: zero per-job lines
  in any of them. Fixed: `setup_logging()` is now the first thing
  `run_agent.py`'s `__main__` block does, in the same position
  `app/main.py`'s lifespan already calls it.
- **CV-extraction error messages were leaking the raw provider
  body.** `gemini.py`'s exception handler used to build
  `GeminiExtractionError` from `f"Gemini request failed: {error}"` —
  a direct interpolation the other two Gemini clients had already
  stopped doing, for exactly this reason. This is how CV 33's
  `extraction_error` column ended up holding Google's complete 429
  body verbatim, quota metric and all — the same leak shape §3
  already warns about, from a path nobody had checked. Fixed: it now
  calls `describe_genai_error(error)`, matching
  `gemini_enrichment.py` and `gemini_embeddings.py`. Pinned by a new
  test.
- **`concurrent_claim_dryrun.py` renamed to `concurrent_claim_probe.py`**,
  closing the "Open after Day 10 Part 4" item above. Every reference
  elsewhere in the repo updated to match; nothing imported it by
  module name.
- **`build_run_summary()`'s CV-embedding visibility gap (flagged when
  `embed_cvs` was proposed, above) is closed, later the same day.**
  Three new keys — `cv_embedding_status`, `cvs_embedded`,
  `cv_embeddings_remaining_null` — now flow from `state.py`'s
  `build_run_summary()` through to `run_agent.py`'s printed summary,
  mirroring the job-side `embedding_status` / `jobs_embedded` /
  `embeddings_remaining_null` fields exactly. This needed more than a
  `state.py` edit: `AgentRunRepository.finish()` copies the summary
  dict onto `AgentRun` columns by key and silently drops anything with
  no matching column, and `tests/test_agent_runs.py`'s three parity
  tests (`test_every_summary_key_has_a_column`,
  `test_columns_beyond_the_summary_are_acknowledged_not_forbidden`,
  `test_the_two_sets_are_the_same_size_apart_from_the_primary_key`)
  would have failed had the three keys been added to the dict alone.
  Fixed with a model change (`AgentRun` gains the three nullable
  columns) and migration `d30da11e80fa`, applied — and `alembic
upgrade head` run — before the `state.py` / `run_agent.py` changes,
  not after. New test
  `test_embedding_fields_reach_the_summary_job_and_cv_side` asserts
  both the job-side and CV-side mappings explicitly; no prior test
  asserted either one.

### Open after Day 14

- **`build_run_summary()` still has no visibility into `embed_cvs`'s
  own counters.** The node writes `stages_attempted` /
  `stages_skipped` / `stages_computed` / `stages_persisted` entries
  like every other node, so a run where it worked or was skipped is
  visible in those generic lists — but there is no
  `cv_embedding_status` / `cvs_embedded` /
  `cv_embeddings_remaining_null` line the way jobs get
  `embedding_status` / `jobs_embedded` / `embeddings_remaining_null`.
  `run_agent.py`'s printed summary and the `agent_runs` row it writes
  both have this gap. Flagged when the node was proposed, not closed
  when it was built — a deliberate scope cut at the time, still open
  now.
  **Closed 2026-09-18, later the same day — done. See the new bullet
  above under "Closed by Day 14."**
- **`_GATE_WINDOW = 25` is a fourth, now-documented cause of
  `notify_eligible` vs `notifications_eligible_selected`
  disagreement.** Its own comment argued the truncation was safe
  because the window and the gate's first condition both order on
  `final_score` — but the other two gates (`semantic_raw`,
  `weight_covered`) are independent of it, so a row outside the top 25
  by score can still pass all three gates while a higher-ranked row
  inside the window fails one of the other two. Not fixed: widening
  the window is a bigger structural change than this pass covers, and
  at ~99 jobs/user the exposure is judged low. **Reconsider once
  jobs/user grows well past current levels** — that is the trigger
  condition, not a calendar date. Both affected comments now say so.
- **Proposed, not designed in code: score and notify a new user
  immediately after onboarding**, instead of making them wait for the
  next scheduled `run_agent.py` pass. Job enrichment already runs
  continuously via the nightly scheduler and jobs stay `is_active`
  between runs, so a newly onboarded user's own CV is the only
  missing input — it just needs to be embedded (already automated by
  `embed_cvs`, above) and scored against the pool that already
  exists. **Open design question, not resolved:** what happens when
  the onboarding-time CV-embedding call fails — a retry path, and how
  the user is told rather than left silently stuck with no
  recommendations and no error — is not yet designed.
  **Closed by Day 15 — done, and the design question answered: no
  retry ceiling, no error shown to the user. See below.**

### Verified live, 2026-09-18 — scoring, gating and delivery confirmed end to end on real data

Two synthetic test jobs — 592 (user 14) and 593 (user 13) — were built
directly from each user's own real profile, preferences and CV
embedding (title, location, work mode, experience bracket and skills
all engineered to score 1.0 on every signal; the embedding copied from
the user's active `cv_versions` row rather than produced by a live
Gemini call, so no quota was spent). Both scored `final_score =
1.000`, cleared all three `is_notify_eligible()` gates, and were
delivered — a real Telegram message, confirmed visually on both
users' own devices. This is the first time the full path (scoring →
gate → delivery) has been confirmed working end to end on live data,
not only on a fixture built to clear the gates, which is what the Day
12 record's "the notify branch has executed" note was describing.
Both jobs are confirmed `is_active = false, is_excluded = false`
(queried 2026-09-18: `592 | False | False`, `593 | False | False`),
each with a one-line note appended to its `description` recording it
was an end-to-end test — `list_scorable_jobs()`
(`app/db/repositories/job.py:580-587`) requires `is_active = true`
first, so neither row can be selected by scoring again regardless of
its embedding or skills. `embedding_model` / `skills_extraction_model`
on both rows read `synthetic:copied-from-cv-version-<id>` /
`synthetic:copied-from-profile-<id>`, not a fabricated real model
name, so neither can later be mistaken for a genuine Gemini result.

---

## 12. Day 15 — instant onboarding notification, and a credential leak

### Closed by Day 15

- **The Day 14 "score and notify a new user immediately after
  onboarding" proposal is built.** After CV extraction reaches
  `ExtractionStatus.COMPLETE`, `app/bot/handlers/onboarding.py`'s
  `_extract_and_notify` now calls
  `_try_instant_recommendation(user_id, version_id)`
  (`onboarding.py:177`), which runs `embed_cv_version()` ->
  `run_scoring()` -> `run_notification_delivery()` in sequence — real
  production calls, nothing stubbed for this path. Delivery is tagged
  `trigger_source="onboarding"`, a fourth value alongside `scheduled`
  and `manual_test` on `NOTIFICATION_TRIGGER_SOURCES`
  (`app/db/models/recommendation.py:69-72`), for the same reason the
  other two are kept apart: so a human asking "did the nightly gate
  ever actually fire for this user" is not misled by a row this path
  put there instead. `run_notification_delivery()` gained a
  `trigger_source` parameter, default `TRIGGER_SOURCE_SCHEDULED`
  (`notification_delivery.py:641`), so every existing caller is
  unaffected.
- **The onboarding-time embed does not get a retry ceiling, and that
  is evidence-backed, not an oversight.** A new `embedding_max_attempts`
  mirroring `enrichment_max_attempts` (`config.py:492`) was designed
  and then NOT built: a live check first showed lifetime `cv_versions`
  embedding history is `n=3` attempts, `3` successes, **zero failures,
  ever** — no case of any kind, deterministic or transient, has
  actually occurred. A ceiling would pre-empt a failure mode nobody
  has observed, on a population too small to have tested the existing
  binary filter (`embedding_attempts == 0`) against anything. Left
  exactly as it was. Revisit only once a real CV-embedding failure is
  observed — that is the trigger condition, not a calendar date.
- **A failure at onboarding time must not silently opt a CV out of the
  nightly `embed_cvs` backstop, and now it can't.**
  `CVRepository.record_embedding_error_without_attempt()`
  (`app/db/repositories/cv.py:306`) writes `embedding_error` for
  diagnosis but leaves `embedding_attempts` untouched. Calling the
  existing `mark_version_embedding_failed()` instead — which the
  nightly batch path still uses, unchanged — would take
  `embedding_attempts` `0 -> 1`, and
  `list_active_versions_needing_embedding()`'s default filter
  (`attempts == 0`) would then exclude that row from every future
  nightly sweep after exactly one onboarding-time failure: worse than
  never having tried. `embed_cv_version()`
  (`app/services/cv_embedding.py:210`) is the only caller of the new
  method. The user is told nothing on failure either way — deliberate:
  nothing here is load-bearing, and the nightly pass redoes it.
- **Verified end to end on live infrastructure, not a fixture, on two
  independent users.** User 14, a fresh unembedded `cv_versions` row
  (id 19, a non-destructive copy of the user's real active version
  17), one synthetic job (id 594) built from the user's own real
  profile/preferences to clear every `is_notify_eligible()` gate —
  same pattern as jobs 592/593 above.
  `_try_instant_recommendation(14, 19)` called exactly as it exists in
  production code, no stubs on any of the three stages. Repeated
  immediately afterward for user 13: a fresh unembedded `cv_versions`
  row (id 20, copy of the user's real active version 18), one
  synthetic job (id 595), `_try_instant_recommendation(13, 20)`. Both
  runs, confirmed via an independent fresh-connection query after
  each: `cv_versions.embedding` populated, a `recommendations` row
  (`final_score = 1.0`), a `notifications` row
  (`trigger_source = 'onboarding'`, `status = 'SENT'`). The real
  `run_notification_delivery()` return value — captured by a
  transparent spy that calls the unmodified function and only
  additionally records what it returned, since
  `_try_instant_recommendation` itself discards it — reported
  `sent: 1` both times: two real Telegram messages, one per user, each
  delivered to that user's own real chat. Cleanup ran in a `finally`
  for each: job 594 / 595 set `is_active=false`,
  `profiles.active_cv_version_id` reverted to 17 / 18 respectively.
- **Incident thirteen.** A Neon Postgres host, username and plaintext
  password leaked into this session's own tool output — not from
  printing `.env` (never touched) but from a Python traceback that
  printed a failing `psycopg` connection call's local variables,
  triggered by `tests/integration/conftest.py` never setting
  `WindowsSelectorEventLoopPolicy` on Windows before this session
  (every script in the repo already does; this fixture never had to,
  because `TEST_DATABASE_URL` had apparently never been set before).
  Rotated on the Neon dashboard immediately. Every other session
  transcript and every temp task-output file were searched for the
  leaked string afterward and came back clean; this session's own
  live transcript file had it 4 times and was deliberately left
  as-is rather than hand-edited mid-session — editing a running
  session's own JSONL risked corrupting resume/rewind for a benefit
  that mostly evaporates once the credential is inert. The
  `conftest.py` event-loop gap is fixed, the same guard every script
  already carries.

### Open after Day 15

- **`_try_instant_recommendation` has no automated test of its own.**
  Its three stages are each covered individually (`embed_cv_version`'s
  integration tests, `run_notification_delivery`'s `trigger_source`
  unit tests, `run_scoring`'s existing coverage), and Day 15's manual
  run verified the full chain once, live — but that was a one-off
  scratchpad script, not a committed test, and it does not run in CI.
- **Incident thirteen fits the pattern §3 already names, and no code
  change closes the pattern itself.** Every leak so far, this one
  included, came from something other than printing `.env` directly.
  "Assume the next one will also not look like a secret operation"
  (§3) is not something a fix retires.

---

## 13. Day 16 — preferences-triggered notification, and nothing real to send yet

### Closed by Day 16

- **Preferences edits now trigger an instant run**, `trigger_source =
'preferences'`. Editing target roles or locations schedules a
  rescore; editing the alert threshold schedules a deliver-only pass
  (no scoring signal reads `notification_threshold` — the gate reads
  it fresh at delivery time); editing the experience bracket triggers
  nothing, because no scoring signal reads either experience field
  either (open question, not a bug — see below).
- **`score_and_notify_user`** (`app/services/notification_delivery.py`)
  is the one shared implementation behind both the onboarding instant
  path and the new preferences-triggered one, with an in-process
  per-user coalescing guard: a second trigger arriving mid-run is
  folded into exactly one rerun rather than starting a second,
  overlapping run.
- **A real onboarding completion-ordering defect is fixed.** A user
  could reach `OnboardingState.COMPLETE` before their own CV finished
  embedding. It survived Day 15 because that day's live verification
  called `_try_instant_recommendation` directly for users whose
  preferences were already filled in, so the real ordering was never
  exercised.
- **`/update_cv` already reached the instant path; now pinned by a
  test**, so a future refactor that breaks that route is caught rather
  than silently regressing.
- **Integration tests ran on a real Neon test database on Windows**
  (41 passed, 0 failed, rerun once after the logging change below with
  the same result).
- **A credential-leak near-miss was caught by tests, not by luck.**
  `logger.exception` in `score_and_notify_user`'s except block would
  have written a rejected Telegram bot token straight into the bot's
  own log — `python-telegram-bot`'s `InvalidToken` embeds the token
  verbatim in its message. Fixed there (only `type(exc).__name__` and
  identifying ids are logged, never `exc_info` or `str(exc)`), and the
  same hardening applied to `_try_instant_recommendation`'s CV-embed
  step, which is exactly incident thirteen's shape (§12): a database
  connection failure whose message can carry a host, a username and a
  plaintext password. `logs/`: 18 files, 0 contain "was rejected by
  the server" (checked as two separate reads, per §0 — a search that
  finds nothing is not the same as a search that had nothing to
  search).
- **The integration test harness now blocks any real
  `TelegramNotifier` construction**, so a test that accidentally
  reaches that far fails loudly instead of risking a real send.
- **Live-verified end to end on 2026-09-23, against the real
  production database and a real Telegram chat — not a fixture.** Job
  695, a clone of job 593, built to score 1.0 on every signal for user 13. Tapping the `/preferences` alert-threshold button from 0.6 to
  0.7 produced exactly one new `notifications` row (id 24,
  `status = 'SENT'`, `trigger_source = 'preferences'`, sent 07:10:08
  UTC), no other user touched, and `scoring_runs.id`'s max unchanged
  at 36 (deliver-only, as designed) — a real Telegram message arrived.
  Tapping back to 0.6 afterward produced nothing at all: no new
  `notifications` row, no message, no new `scoring_runs` row.
  `select_notifiable()`'s `evaluation.already_sent` check
  (`app/services/notification_delivery.py:379`) makes a second send
  structurally unreachable once one `SENT` row exists for a pair, not
  merely unlikely in practice. Both outcomes matched a prediction
  written down before either phone step, exactly. Job 695 deactivated
  and user 13's preferences confirmed restored to their exact pre-test
  row afterward.
- **Process note.** During this same live verification, a "confirmed"
  report was sent before the phone action it described had actually
  happened, which made the first round of reads come back empty and
  looked briefly like a real delivery failure — a repeat of the
  documented, unresolved shape in §1's row about the two `/preferences`
  taps that got no reply. It wasn't that shape this time: process,
  not code. Rule for future live-verification stages: the human
  completes the phone step and waits first, then sends confirmation
  — never the reverse.
- **`files (1).zip` was found sitting at the repo root, gitignored
  (`.gitignore`'s `*.zip`) and therefore invisible to a routine `git
status`, even with `--untracked-files=all`.** `verify_archive.py`
  flagged it FORBIDDEN before anything was shared: it contained a
  nested `AI_JOB_HUNT_AGENT_day12.zip` (148,212 bytes) alongside
  `DAY_12_REPORT.md`, `TEST_RESULTS.md`, and `MVP_LIMITATIONS.md` — the
  nested archive's name and size make it most likely the incident
  twelve archive itself (§9), left behind rather than a fresh leak.
  The nested zip was never inspected further — `verify_archive.py`
  does not recurse into one — so its own contents remain unconfirmed.
  Deleted by hand rather than shared or extracted.
- **The roles/locations rescore branch of the instant path delivered a
  REAL Adzuna job live, 2026-09-23.** Job 19 ("Assistant Manager -
  Python Development", BNP Paribas, Bangalore, Karnataka) was
  delivered to user 13 as `notifications` id 25, `status = 'SENT'`,
  `trigger_source = 'preferences'`, from `scoring_runs` id 37
  (`started 09:41:32`, `finished 09:42:56` UTC), Match 73%
  (`final_score = 0.7259608719211588`). Every prediction made before
  the phone step — the notification row, its fields, the scoring run
  id, and the score — matched the actual read exactly.
- **Location edits (`scoring_runs` 38, 39) and the restore edits (40, 41) sent nothing, verifying `select_notifiable()`'s `already_sent`
  check live on a real job, not only a synthetic one.** Run 39
  (final Bangalore preferences) still found job 19 eligible
  (`final_score = 0.9134608719211588`) but skipped it as already sent
  rather than sending a duplicate, because one `SENT` row already
  existed for the pair from run 37's delivery.
- **The in-process coalescing guard
  (`_in_flight_users`/`_rerun_needed_users` in `score_and_notify_user`)
  was observed live, twice.** Two `/preferences` edits landing roughly
  a minute apart, on two separate occasions, each produced exactly two
  sequential `scoring_runs` rows with the second starting about 3
  seconds after the first finished (39 after 38; 41 after 40) — never
  overlapping, consistent with the second edit's trigger being folded
  into the first call's rerun loop rather than two independent runs
  racing each other.
- **`scripts/preference_match_probe.py`: read-only, no writes, no
  Telegram, no Gemini.** Reads candidate jobs (`is_active`, not
  excluded, non-`synthetic_test`, `skills_extracted_at` not null, no
  `SENT` row) joined to their stored `recommendations` row for a
  given user; keeps the CV-derived skill/semantic/experience signals
  as stored and recomputes only title/location/quality/`combine()`/
  `is_notify_eligible()` through the real functions, never
  reimplemented — including the real `parse_list_input` for typed
  free-text preferences, so the stored form of a typed string is
  guaranteed to match what `/preferences` would actually store.
  Self-check: reconstructs the stored `final_score` for 7 rows under a
  user's CURRENT preferences, delta `0.000e+00` against a `1e-9`
  tolerance for all 7. Also runs a whole-pool simulation over every
  candidate under a hypothetical preferences state. Cannot call
  `select_notifiable()` on a hypothetical state — it reads stored
  preferences/recommendations directly with no override parameter — so
  the probe does not model `_GATE_WINDOW` or
  `max_notifications_per_user`; it says so explicitly rather than
  silently approximating them.
- **Restore confirmed.** User 13's `user_preferences` row matches the
  Stage B baseline field by field (`target_roles`,
  `preferred_locations`, `remote_only`, `min_experience_years`,
  `max_experience_years`, `notification_threshold`), and job 19's
  stored `recommendations` row returned to
  `final_score = 0.5384608719211589` — the same value Stage A's
  self-check had already measured for this pair under the original
  preferences.
- **Commit `ed37720` (2026-09-23 12:58 IST, 61 files) audited,
  read-only, and closed.** It was made with VS Code's auto-message
  commit button, the same tool behind the previous 10 commits. Findings:
  `.env` was never committed on any branch — only `.env.example` ever
  was; no `storage/`, `logs/`, `*.zip`, `*.pdf` or `*.docx` has ever
  been committed, anywhere in history; the 39 scratch files added in
  `ed37720` (`check_url.py`, `scratch_query.py`, `scratch_users.py`, 36
  `q_*.sql` files) hold 0 credentials and 0 personal data by pattern
  scan; the remote URL holds no embedded credentials. The auto-tool's
  bracket label is unreliable as a signal of what a commit touched —
  `c800eed`'s "chore(.env)" bracket named `.env.example`, not `.env`,
  and a second commit's "chore(cv_text)" bracket named a source file
  (`app/services/cv_text.py`), not a document. **Rule going forward:
  never click Commit or Sync in VS Code while an agent is working** —
  a mid-session auto-commit stages and ships whatever the working tree
  holds at that instant, without the review this audit just did by
  hand.

### Do not "fix" these either — additions to section 1

| Observation                                                                                                              | Looks like                                   | Actually                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| ------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `scripts/score_jobs.py --explain JOB_ID` looks like a read-only inspection of a stored row                               | harmless, safe to run any time               | `--explain` is skipped entirely under `--dry-run` (own code: "nothing was written, so ... would only show stale data ... Skipped"), so every real use of `--explain` first runs a full, non-dry scoring pass and writes a new `scoring_runs` row. There is no way to inspect a stored explanation without also writing a new run — read the stored `recommendations` row directly instead when a live `scoring_runs.id` must stay fixed (Day 16 Stage 3.4) |
| `scripts/send_test_notification.py --send` delivers a message for a job its own gate report marked `blocked`             | the tool ignoring its own gate, a bug        | deliberate, and documented in its own module docstring: it selects the top-ranked unsent candidate by rank, "NOT gated," specifically so it tests delivery independently of the gate. Its rows are always `trigger_source = 'manual_test'` for exactly this reason — a `manual_test` row is proof the send path works, never proof `is_notify_eligible()` passed                                                                                           |
| `notifications` rows with `trigger_source = 'preferences'` appear at arbitrary times of day, not on the nightly schedule | a scheduler bug, or a run outside its window | expected. Preferences edits fire `score_and_notify_user` live, from the Telegram handler, the moment a button is tapped — there is no schedule to be outside of for this trigger source                                                                                                                                                                                                                                                                    |

### Open after Day 16

- **Cross-process duplicate send is still unguarded against the
  nightly `run_agent.py`.** The partial unique index on
  `(user_id, job_id) WHERE status = 'SENT'` fixes the bookkeeping (at
  most one `SENT` row ever lands) but not a second real message if the
  interactive bot and a nightly run both attempt the same pair inside
  the same race window. Precedent for the fix:
  `CVRepository.claim_for_extraction`'s atomic conditional UPDATE.
  Revisit if a duplicate is ever actually observed, or before adding a
  second always-on process.
- **The experience preference is stored and never read by scoring.**
  Neither `min_experience_years` nor `max_experience_years` is read by
  any signal in `app.services.scoring_signals` or
  `app.services.job_scoring`, so editing it through `/preferences`
  triggers no rescore and no redelivery because nothing could change.
  Whether that's correct (the field exists for future use) or a real
  gap (a scoring signal should exist and doesn't) is a product
  decision, not something to guess at in code.
- **`/update_cv` runs are tagged `trigger_source = 'onboarding'`.**
  Whether a CV replacement by an already-onboarded user deserves its
  own trigger source, the way preferences got one, is undecided.
- **`normalize_location`'s locality limitation is real and unfixed.**
  On 2026-09-23, 7 Delhi jobs scored `location = 0.0` for user 13
  despite both being in Delhi ("Sansad Marg, New Delhi", "South Delhi,
  Delhi", "Maurya Enclave, North West Delhi" vs. the user's plain
  "Delhi" preference) — a locality-vs-city string mismatch, not a
  false negative on the city itself. Remote jobs score 1.0 regardless
  of city, which is an intentional rule, not related to this gap. The
  Day 6 rule that two users of one location-matching implementation
  must share it (rather than one growing a special case) constrains
  any fix here.
- **Pre-existing log hygiene left alone, five sites**, per this stage's
  own hard rule not to touch them: `notification_delivery.py:577, 623,
634` (`logger.exception`), `app/bot/handlers/onboarding.py:348` and
  `app/bot/handlers/preferences.py:85` (`logger.debug(...,
exc_info=True)`). Same leak shape as the two sites fixed this Day —
  not yet fixed themselves.
- **Unexplained: scoring run 33 printed no `jobs_remote` line; run 34,
  same inputs, printed `jobs_remote 5`.** Not chased further this Day.
- **Environment flake, not reproduced:**
  `test_the_notify_branch_delivers_and_records_an_attempt` failed once
  with "server closed the connection unexpectedly" against the Neon
  test database; passed on two immediate reruns. Recorded rather than
  dismissed, per this project's own rule about single unexplained
  failures.
- **No real (non-synthetic) job currently clears all three
  notification gates for any user at the default 0.6 threshold.** The
  best real score observed for user 13 is roughly 0.57, with
  `weight_covered` capping out around 0.50. This Day's live
  verification proves the instant path itself works end to end; it
  does not mean a real job will trigger it. The causes are already on
  record and are not new: the enrichment quota backlog (§10), the
  empty `adzuna_query_keywords` / `adzuna_query_locations` (§10), and
  the `normalize_location` locality limitation immediately above.
  **Still true under user 13's real, untouched preferences — Day 16b
  confirms rather than overturns this.** A real job (19) clears all
  three gates only when `target_roles`/`preferred_locations` are set
  through `/preferences` to match it exactly; under the account's
  actual, restored preferences (`Backend Engineer` / `Delhi`), job
  19's own `final_score` is `0.538460872`, still short of 0.6.
- **A delivered job's apply link was dead, and the code explains
  exactly why `is_active` didn't already catch it.** Job 19's Adzuna
  apply link returned "Page not found" on 2026-09-23, 23 days after
  ingestion (`created_at = 2026-08-31 16:47:47`) — yet
  `is_active = true`. `job_retire_after_days` (default 21,
  `app/core/config.py:150`) is the retirement mechanism, applied only
  inside `_retire_stale_jobs()`
  (`app/services/job_ingestion.py:478-523`), called once per ingestion
  run (`app/services/job_ingestion.py:322`) — never on the nightly
  scoring/notification pass, never on any separate schedule. It marks
  a job inactive only if `last_seen_at` (not `created_at`) is older
  than the cutoff, via `JobRepository.retire_unseen_since()`
  (`app/db/repositories/job.py:113-133`), and only if a successful
  ingestion run occurred within the last
  `job_retire_requires_run_within_days` (default 3,
  `app/core/config.py:154`) — the interlock that stops one missed week
  from silently retiring the whole table (see §10's `_retire_stale_jobs`
  docstring). Traced with real data, not guessed: job 19's
  `last_seen_at = 2026-08-31 16:57:49` (seen once, 10 minutes after
  creation, never returned by an Adzuna search again). The last
  ingestion run, id 11, started `2026-09-21 04:18:27`; its cutoff was
  `2026-08-31 04:18:27`, and job 19's `last_seen_at` was still
  ~12h39m inside that cutoff, so run 11 correctly left it active
  (`retired = 0`, confirmed from the row). Job 19 crossed the
  21-day-unseen line about 12 hours later, on 2026-09-21 ~16:57:49 —
  after run 11 had already finished checking. The scheduled
  `AIJobHuntAgentIngestion` task has not run since
  (`NextRunTime = 2026-09-27`), so nothing has re-evaluated retirement
  since job 19 became eligible for it. Not a bug: the check ran, was
  correct at the moment it ran, and simply hasn't run again since the
  job crossed the line. **Corrects a misattribution made earlier in
  this same test sequence** — an intermediate Stage C report cited
  this mechanism as "CLAUDE.md §1"; it is not a §1 row, it is
  documented in `app/core/config.py:150`, `app/db/models/job.py:104`,
  and `docs/Day_6_JobIngestion.md:461-465`. **Options are a product
  decision, not decided here:** shorten `job_retire_after_days`, add a
  link-liveness check before send, or deactivate jobs missing from a
  later ingestion pass by some signal other than `last_seen_at`.
- **Every `/preferences` roles or locations edit triggers a full
  rescore even when the typed value equals the value already
  stored.** `PreferencesService.handle_text` never compares old
  against new before setting `preferences.target_roles = roles` /
  `preferences.preferred_locations = locations` and returning
  `recommendation_trigger = "rescore"` — confirmed live: retyping
  "Delhi" (the value already stored) produced `scoring_runs` id 38
  exactly like every other edit. Each rescore in this Day 16b sequence
  took 84-128 seconds against 689 active jobs for one user (37: 84s;
  38: 103s; 39: 87s; 40: 106s; 41: 128s). Not a bug — recorded as a
  cost: a user editing back and forth pays a full rescore per tap
  regardless of whether anything could actually change.
- **Adzuna redirect URLs carry a `utm_source` query parameter that may
  be the Adzuna `app_id`** — job 19's `url` is
  `https://www.adzuna.in/details/5863071574?utm_medium=api&utm_source=5ee593b0`,
  stored in `jobs.url` and sent verbatim in every notification link.
  Deliberately unverified: not compared against `.env` or
  `settings.adzuna_app_id`, and no credential was printed, per §3.
  `app_key` does not appear to travel in these URLs. Already covered
  by the pending Adzuna rotation (§7) — recorded as another place that
  rotation should account for, not a new leak.

### Findings (process) — Day 16b

- **Three false starts in Stage C: once for Edit 1, twice for Edit
  2 — a "done" confirmation was sent before the phone step had
  actually happened.** Each time, the database reads correctly showed
  no change: `user_preferences.updated_at` unchanged, no new
  `scoring_runs` row, no new `notifications` row. The second Edit 2
  attempt made it worse: the confirmation message itself claimed the
  bot had replied "Updated target locations" — a claim that came from
  a message template, not from the screen — producing a wrong "save
  reply sent but write missing" hypothesis, withdrawn once a Telegram
  screenshot showed no such reply had ever been sent. **Rule going
  forward:** a phone-step confirmation must come from the screen — a
  screenshot, or the bot's exact reply quoted with its own timestamp —
  never from a pre-written template. And the first read after any
  phone step should be `user_preferences.updated_at`: it is what
  separates "nothing reached the bot" from a real persistence bug,
  before any other read is worth running.
- **The 2026-09-23 10:28 IST message for job 403 (Kuoni Tumlare, Match
  55%, below the 0.6 threshold) is `notifications` id 23,
  `trigger_source = 'manual_test'`.** Not a gate failure — see the
  section 1 row on `scripts/send_test_notification.py --send`, which
  delivers its top-ranked unsent candidate deliberately ungated,
  specifically to test delivery independent of `is_notify_eligible()`.
  It arrived from a manual test run, not from the scheduled nightly
  pass or from anything in this Day 16b sequence.
