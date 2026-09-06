# Hotfix session — 2026-09-05

Two hotfixes applied outside the Day-N flow, delivered as zip bundles
from outside the repo, verified against a real running server and a
real Telegram bot rather than against the test suite alone. This file
is a record for another agent picking this up cold — what was broken,
what changed, what was verified, and what is still open. It is not a
Day-N progress file because no feature work happened; both fixes are
production incidents against code that shipped in earlier days.

Test counts, as two numbers each, not deltas — per §2 of `CLAUDE.md`:

| Point           | Result                                                           |
| --------------- | ---------------------------------------------------------------- |
| Before hotfix 1 | not run this session (prior state was untested against `run.py`) |
| After hotfix 1  | **709 passed, 23 skipped**                                       |
| After hotfix 2  | **714 passed, 24 skipped**                                       |

---

## Hotfix 1 — `run.py` bypassed the event loop policy

**Symptom.** `python run.py` logged `Application startup complete`, the
bot connected, but every database-backed handler died with
`psycopg.InterfaceError: Psycopg cannot use the 'ProactorEventLoop'`.
The service looked healthy in every log line before that.

**Cause.** `run.py` set `WindowsSelectorEventLoopPolicy` and trusted
`uvicorn.run()` to read it. uvicorn 0.36.0 replaced
`Config.setup_event_loop` with `Config.get_loop_factory`, and
`Server.run` now calls `asyncio.run(..., loop_factory=self.config.get_loop_factory())`.
An explicit `loop_factory` bypasses the event loop policy entirely, so
the policy call became dead code — silently. `requirements.txt` pins
`uvicorn==0.52.4`, well past that change. Not a Day 12 regression; no
Day 12 file touches the event loop.

**Fix.** `run.py` now builds `uvicorn.Config` and `uvicorn.Server`
directly and passes its own `loop_factory` returning
`asyncio.SelectorEventLoop()`, rather than calling `uvicorn.run()`. The
`WindowsSelectorEventLoopPolicy` call is kept even though uvicorn no
longer reads it — other loops created in the process without an
explicit factory still consult it.

**Files copied, unmodified, from the hotfix bundle:**

- `run.py` (replaced)
- `tests/test_entrypoint_loop.py` (new) — parses `run.py` with `ast`
  rather than grepping it, because the module's own docstring mentions
  `uvicorn.run()` in prose and a substring check would flag its own
  documentation.

**Verification.**

```
python -m pytest -q
709 passed, 23 skipped, 4 warnings in 139.94s
```

Then `python run.py`, startup log:

```
INFO:     Started server process [16788]
INFO:     Waiting for application startup.
2026-09-05 10:59:09,646 | INFO | app.db.session | Database engine created.
2026-09-05 11:04:02,519 ...  <- see hotfix 2, this is where it broke next
```

Confirmed live: `/start`, `/profile`, `/update_cv` all answered, and a
CV upload was accepted and stored. This is the part of the symptom the
fix actually closes.

---

## Hotfix 2 — a NUL byte in a CV's PDF text layer

**Symptom.** Immediately after the CV upload above, `/profile` at
11:04 returned "Something went wrong on my side." Traceback recovered
from the running server's captured stdout (not `logs/`, which was
empty — nothing in this project writes there yet):

```
psycopg.DataError: PostgreSQL text fields cannot contain NUL (0x00) bytes
...
File "app/bot/handlers/onboarding.py", line 144, in _extract_and_notify
    result = await extract_cv(user_id)
File "app/services/cv_extraction.py", line 172, in extract_cv
    version = await cv_repo.create_version(
...
[SQL: UPDATE cvs SET raw_text=%(raw_text)s::VARCHAR, updated_at=now() WHERE cvs.id = %(cvs_id)s::INTEGER]
[parameters: {'raw_text': 'VARUN NARAD\nCybersecurity Engineer\n\x00918700430994 ...', 'cvs_id': 30}]
```

**Cause.** A PDF text layer is not clean text: when a glyph has no
mapping in the font's encoding table, `pypdf` returns `\x00`. On this
CV that happened where the `+` of an international phone number
belonged. PostgreSQL refuses a NUL in `text`/`varchar`/JSONB outright.
Nothing between the extractor and the column removed it. Raised in
`extract_cv`'s **third** phase — after the Gemini call had already
been spent. Latent in every PDF path; the three CVs extracted earlier
in the project's history simply didn't happen to contain one.

**Why no test caught it.** Every fixture in the suite fed the
extractors clean text. A `.docx` cannot carry a NUL at all —
`python-docx` refuses to write one — so only the PDF path was ever
exposed, and nothing exercised it with realistic `pypdf` output.

**Fix.** `app/services/cv_text.py` strips NUL bytes at the extraction
boundary — before the text reaches either the model or the database —
and logs how many it removed (count only, never the text). Cleaning
there rather than at the write is the point: the same string goes to
`cvs.raw_text` and to the Gemini call that produces
`cv_versions.extracted_profile`, so the two cannot disagree. Removed,
not replaced with a space — the NUL sat inside a token (mid phone
number), and a space would split it rather than repair it.

**Files copied:**

- `app/services/cv_text.py` (replaced)
- `tests/test_cv_text_nul.py` (new)
- `tests/integration/test_candidate_pipeline.py` (replaced — verified
  by diff before copying: identical to the existing file plus one new
  test, `test_a_nul_byte_in_the_text_layer_reaches_the_database_cleaned`,
  appended at the end)

**Verification.**

```
python -m pytest -q
714 passed, 24 skipped, 4 warnings in 139.31s
```

Server stopped (old PIDs) and restarted; startup log clean, same shape
as hotfix 1's. CV re-uploaded live. Server log:

```
11:16:38 | INFO | app.services.cv_intake | Stored CV for user_id=2 type=pdf bytes=187511
11:16:39 | INFO | app.services.cv_text | Stripped 8 NUL byte(s) from a pdf text layer before storage
11:17:09 | INFO | app.services.cv_extraction | Extraction complete for cv_id=31 user_id=2 skills=33
```

`/profile` returned a full profile (role, location, summary, 21+
skills, two recent roles, education) pulled from the previously-fatal
file.

**Left deliberately unfixed, per the hotfix's own notes — a decision,
not a gap:** phase-3 failures still leave `extraction_error` NULL. The
row self-heals via the stale-claim window (`DEFAULT_STALE_AFTER`, 15
min), so `cv_id=30` from before the fix needed no manual repair.

---

## Both bundles arrived by an unusual route, and were verified before trusting them

Both hotfix zips arrived in `Downloads` as `files (N).zip` rather than
under the name named in the task — indistinguishable at a glance from
the exact filename (`files (1).zip`) recorded in `CLAUDE.md` as part of
a real credential/CV leak incident. Before copying anything into the
repo, each was extracted **outside** the repo (scratch directory) and
its entries were listed with `[System.IO.Compression.ZipFile]::OpenRead`
— never blind `Expand-Archive` into the tree — to confirm the contents
were exactly the expected files (`run.py` / hotfix docs / tests) with
no `.env`, `storage/`, or `logs/` present, before any file touched the
working tree. Worth recording so the next agent doesn't have to
re-derive why that step exists.

---

## Follow-up: does scoring/ranking actually work, and does notification fire?

This is the part that isn't a hotfix — it's verifying a claim
("scoring and ranking working properly") against real, current data
rather than assuming green tests mean the feature works end to end.

### A live example of CLAUDE.md §0 ("a success status is not success")

`python -m scripts.score_jobs --user-id 2 --top 20 --bottom 10` was run
immediately after hotfix 2, before the new CV had been embedded. The
funnel it printed said:

```
status                    no_scorable_users
users_skipped_no_cv       1
  cv_not_embedded         1
users_scored              0
pairs_scored              0
```

— i.e., nothing was computed. But the `--top`/`--bottom` tables below
it printed a full, plausible-looking ranked list anyway (top job at
`final_score 0.983`). Per the script's own docstring, `--top`/`--bottom`
**read `recommendations` directly and never recompute** — so this was
stale data left over from an earlier run against a _different_ CV
version, printed directly underneath a status saying nothing had been
scored. Nothing was wrong with the script; the trap is real and is
recorded here so it isn't rediscovered as a bug.

### Closing the actual gap: the CV wasn't embedded

```
python -m scripts.embed_cvs --dry-run    # confirmed 1 candidate, API calls budget 1
python -m scripts.embed_cvs              # candidates 1, attempted 1, failed 0, API calls 1
                                          # STILL WITHOUT AN EMBEDDING: 0
python -m scripts.embed_cvs --dry-run    # missing embedding 0, API calls budget 0 (today's quota spent)
```

Succeeded on the first attempt, no 429. Today's embedding quota is now
0 — do not attempt another `embed_cvs` run today.

### Re-scored against the real, current CV

```
python -m scripts.score_jobs --user-id 2 --top 20 --bottom 10
```

Real numbers this time — `users_scored 1`, `pairs_scored 98`. Before
vs. after, as two separate numbers per §2 rather than a computed delta:

|                    | Stale CV (misleading, printed under a `no_scorable_users` status) | Current CV (real)                                             |
| ------------------ | ----------------------------------------------------------------- | ------------------------------------------------------------- |
| Best `final_score` | 0.983                                                             | **0.537**                                                     |
| `notify_eligible`  | 0                                                                 | 0                                                             |
| Failing gate       | `weight_covered` 0.500 < 0.55 only                                | `final_score` 0.537 < 0.70 — the harder gate, not a near miss |

Contributing factors visible directly in the printed rows and funnel:

- `abstain_skill 96/98`, `abstain_experience 98/98` — unchanged
  pre-existing data gap (§1 of `CLAUDE.md`: most jobs are unenriched).
- **Title score 0.000 on almost every top-20 row**, vs. `1.000` on the
  stale CV's top matches — this CV's target roles do not overlap this
  job pool's titles the way the previous CV's did.
- **Location score 0.000 on most rows** — stored location preference
  does not match "Faridabad, India".
- `quality_penalty_agency 29`, `quality_penalty_no_city 23` pulling
  `quality_multiplier` below 1.0 on many rows.

Conclusion, stated as a fact rather than a verdict: for this specific
CV against this specific 99-job dataset, no pair is close to the
notification gate. Not a bug, not a config problem, and not a coverage
gap — the previous CV on file was an AI/ML Engineer CV, the current
one is a Cybersecurity Engineer CV, and the 99-job pool was ingested
with AI/ML keywords. Title score reads 0.000 on almost every row
because almost none of these job titles overlap "Cybersecurity
Engineer" — that is the correct output of the title signal against
this pool, not a defect in it.

### The scheduler does not run, and this was checked, not assumed

```
Get-ScheduledTask -TaskName "*agent*"
```

returned nothing for this project (`SpaceAgentTask` exists and is
unrelated). `app/main.py` has no internal scheduler, loop, or cron.
This confirms, on this machine, what `CLAUDE.md`'s Day 10 Part 3 /
Day 12 records already state: the nightly run has never been observed
to fire, because nothing triggers it. Waiting — of any duration — does
nothing until either `scripts/schedule_agent.ps1` registers a real
Windows Scheduled Task, or `scripts/run_agent.py` / `scripts/score_jobs.py`
is invoked by hand.

---

## Open after this session

- **Credential rotation is still not done.** Unrelated to this
  session's work but still the standing, unresolved item from every
  prior record.
- **Today's CV-embedding quota is spent** (`API calls budget 0`). Any
  further CV re-embedding must wait for quota reset.
- **No Windows Scheduled Task is registered for this project.**
  Registering one is a deliberate step (`scripts/schedule_agent.ps1`),
  not something that happens by waiting.
- **`notify_eligible = 0` for user 2 is current, verified, and real**
  — not a stale read, not a config bug. Whether to treat the
  title/location mismatch as a preferences problem, a scoring-weight
  problem, or simply "this CV doesn't match this job pool" is a
  decision for a human, not something this session changed.
- **The binding gate changed CV to CV, and any earlier note about
  fixing it applies only to the old one.** Under the stale/previous
  CV, the failing gate was `weight_covered 0.500 < 0.55` — a near
  miss, and the kind of gap that more job enrichment could plausibly
  close. Under the current CV, the failing gate is
  `final_score 0.537 < 0.70`, and enrichment cannot close that: `title`
  and `location` both score `0.000` on almost every row, and neither
  is a coverage problem — enriching a job's skills/experience does not
  change whether its title or city matches. Any earlier record in this
  session (or elsewhere) saying "enrichment will make the gate fire"
  was true for the AI/ML CV and is not true for the Cybersecurity CV
  now on file.
- **CV id 30** (the one that hit the NUL byte before the fix) never
  needed manual repair — confirmed self-healed via the stale-claim
  window, superseded by CV id 31.

---

## Findings captured before a clean-slate deletion (2026-09-05, later same day)

Both of these exist only as rows in tables about to be wiped by a
"delete all users" pass. Recorded here because they are otherwise gone
the moment that deletion runs, per §0 — a row deleted without being
written down is indistinguishable from a row that never existed.

### a) User 10 — `remote_only=True` zeroes every job, right now, for real

```sql
SELECT work_mode, COUNT(*) FROM jobs GROUP BY work_mode ORDER BY work_mode;
```

```
work_mode | count
------------------
None      | 99
```

**All 99 jobs have `work_mode = NULL` today** — not 94 of 99 as an
earlier record in this file (and in `CLAUDE.md`) states; the data has
drifted further since that was written. Zero jobs are confirmed
`remote`, `hybrid`, or `onsite`.

```sql
SELECT user_id, remote_only FROM user_preferences ORDER BY user_id;
```

```
user_id | remote_only
--------------------
2       | False
3       | False
10      | True
```

```sql
SELECT COUNT(*) AS total,
       COUNT(*) FILTER (WHERE location_score = 0)    AS zero,
       COUNT(*) FILTER (WHERE location_score IS NULL) AS abstained,
       COUNT(*) FILTER (WHERE location_score > 0)    AS positive
FROM recommendations WHERE user_id = 10;
```

```
total | zero | abstained | positive
------------------------------------
98    | 98   | 0         | 0
```

**All 98 of user 10's scored pairs have `location_score = 0.0`.** Not
abstained — genuinely scored zero. `score_location`
(`app/services/scoring_signals.py:192-197`) has a rule
`if remote_only and job_work_mode != "remote": return 0.0`, and since
every job's `work_mode` is `NULL` (≠ `"remote"`), that rule fires for
every row. This is not hypothetical and not a leftover-data artifact —
it is the current, correct-per-the-code output of `remote_only=True`
against a dataset where no job has ever been confirmed remote. It is
also the live precedent for why a naive symmetric "on-site only" flag
(§ the Stage 3 work-mode investigation, reported in chat, not yet
written to a file) would zero out all 99 jobs the same way, from the
opposite direction.

### b) User 2 — current scoring, before the account is deleted

```sql
SELECT COUNT(*) AS total, MAX(final_score) AS best_final_score
FROM recommendations WHERE user_id = 2;
```

```
total | best_final_score
--------------------------
98    | 0.5370706341904803
```

```sql
SELECT COUNT(*) FILTER (WHERE skill_score IS NULL)      AS abstain_skill,
       COUNT(*) FILTER (WHERE experience_score IS NULL) AS abstain_experience,
       COUNT(*) FILTER (WHERE title_score = 0)           AS title_zero,
       COUNT(*) FILTER (WHERE location_score = 0)        AS location_zero,
       COUNT(*) FILTER (WHERE location_score IS NULL)    AS location_abstain
FROM recommendations WHERE user_id = 2;
```

```
abstain_skill | abstain_experience | title_zero | location_zero | location_abstain
------------------------------------------------------------------------------------
96            | 98                 | 97         | 75            | 23
```

Best `final_score` across all 98 pairs is `0.537`, below the `0.70`
notification threshold — so `notify_eligible` is 0 for user 2 by
construction; no pair reaches the floor the other two gates are
compared against. `title_score` reads `0.000` on 97 of 98 rows and
`location_score` is `0.000` (not abstain) on 75 of 98, with the
remaining 23 abstaining rather than scoring — none scoring positive.
As recorded earlier in this file: this reflects a genuine CV-to-job-pool
mismatch (a Cybersecurity Engineer CV against a pool ingested with
AI/ML keywords), not a scoring defect and not something a clean slate
changes.

### c) Two orphans found while reconciling `storage/cvs/` against the database, before the clean-slate deletion made them invisible

Found while listing `storage/cvs/` filenames for the Stage 2 deletion
report, not investigated further — recorded here as findings only,
per instruction, before the folder was cleared.

- **`storage/cvs/12/` had no corresponding row in `users`.** The
  `users` table held only ids 2, 3 and 10 at the time; folder `12`
  existed anyway, empty. Something created that folder for a user that
  no longer exists in `users` — or never fully did.
- **`storage/cvs/2/` held 21 files, but `cvs` had only 20 rows for
  `user_id=2`.** Diffing the 21 on-disk filenames against the 20
  `storage_path` values on file: `4e70c8b6a5064012b01f784d485c46f6.pdf`
  was on disk with no `cvs` row pointing at it anywhere.

**This is a real finding about the intake path, not about this
deletion.** A file written to disk with no row committed alongside it
is the shape of `CVIntakeService.save()` (or the `cvs` insert that
follows it) succeeding and failing at two different points instead of
one — the file lands, and something after it does not. It predates
this session and predates the clean-slate deletion; it is recorded
here **because** the deletion that followed removed the one piece of
evidence (the orphaned file itself) that made it visible at all. Not
investigated and not fixed, per instruction — named only.
