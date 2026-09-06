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

|                             |                                                                                 |
| --------------------------- | ------------------------------------------------------------------------------- |
| Alembic head                | `c8e2a15f4b93` — 10 migrations; `alembic check` clean as of Day 12              |
| Tests                       | 729 passing with a database, 706 + 23 skipped without (664 before Day 12)       |
| Workflow                    | `app/workflows/` — 8 nodes, 3 conditional edges; runs persisted to `agent_runs` |
| Jobs                        | 99, all embedded, 1 excluded (job 2)                                            |
| CV versions                 | 3 active, all embedded                                                          |
| Enriched jobs               | 5, of which 2 produced skills                                                   |
| Jobs with experience bounds | 0                                                                               |
| Active scoring signals      | **3 of 5**                                                                      |
| Notifications sent          | 1, all `trigger_source = manual_test`; **0 from the gate**                      |
| Feedback rows               | 0                                                                               |

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
