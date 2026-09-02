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

| Observation | Looks like | Actually |
|---|---|---|
| `abstain_experience = 98 / 98` | extraction is broken | source data ceiling — descriptions truncated at Adzuna's 500-char cap; only 37 of 99 mention years at all |
| `notify_eligible = 0` | the gate is broken | the gate is working: `weight_covered` 0.50 < `min_weight_covered_to_notify` 0.55 |
| signal columns on `recommendations` are NULL | missing data, default them to `0.0` | **NULL *is* abstain.** Defaulting to 0.0 destroys the entire abstain model. A test exists to keep them nullable — do not relax it |
| `PARTIAL` / `FAILED` never appear in `scoring_runs` | dead enum members, delete them | nothing sets them yet |
| `jobs_remote` and `jobs_hybrid` both 0 | counter is broken | `work_mode` is NULL on 94 of 99 jobs |
| company matching is exact, not substring | the `\|\|` multi-company field is being missed | deliberate — see Day 8 record §3.2 |
| `list_needing_enrichment()` does not filter `is_excluded` | wasted API quota on job 2 | deliberate — having skills and being scorable are different questions (§8.4) |
| dry-run prints `missing skills 91` and `would enrich 97` | the numbers should agree | they count different things; both correct (§8.5) |

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

| | |
|---|---|
| Alembic head | `9a4e7c1d5b82` — 8 migrations |
| Tests | 363 passing |
| Jobs | 99, all embedded, 1 excluded (job 2) |
| CV versions | 3 active, all embedded |
| Enriched jobs | 5, of which 2 produced skills |
| Jobs with experience bounds | 0 |
| Active scoring signals | **3 of 5** |

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
  a job with *missing* data can outrank a job with *bad* data. Observed
  on real data and verified by hand. This is abstention applied
  consistently — but nobody has decided it. **It needs a decision, not
  a patch.**
- **One company field holds three companies joined by `||`.** Exact
  match will never catch it. Undecided.
- **The agency list may be incomplete** — 6 more candidates found, which
  would take affected pairs from 29 to 35. Undecided.
- **`--top` prints the `title` header twice.** Cosmetic.

### Day 9 decisions still open

1. `langgraph` import location — `app/agent/` or behind a wrapper in
   `app/integrations/`?
2. Is `embed_jobs` a Day 9 node? It is not in the plan's Day 9 row, but
   without it an ingest-then-score run scores none of the new jobs and
   the funnel balances perfectly while doing it.
3. `agent_runs` table now (9th migration) or on Day 10?
4. Build the notify-branch reachability probe, or skip it?

See `docs/Day_9_Design_Note.md`.

---

## 8. Prompts and staging

Multi-step work is written as staged prompts with hard rules at the
top, exact file paths, exact find/replace strings, a table of expected
test values, and explicit verification commands. **Stop and report
between stages.** Do not run stage 3 because stage 2 looked fine.