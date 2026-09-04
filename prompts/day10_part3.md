# Day 10, Part 3 — persistence and unattended operation, end to end

`CLAUDE.md` is loaded. Read it before starting. Sections 0 and 1 apply
throughout.

**Work this through in one session without stopping for approval.**
This overrides `guard.md`'s "then stop and wait" for this document only.
Still run the checklist for every code change — but write your answers
into the progress record as you go instead of pausing on them, and keep
moving. The stop conditions in §0.2 are the only things that should
halt you.

Everything you find, decide, reject or are surprised by goes into
`docs/Day_10_progress.md` **as you go**, not reconstructed at the end. A
human will read that file afterwards to write the teaching record, so a
finding that only exists in your reasoning is a finding that is lost.

**Task 0 comes before the other three and is not optional.** It is
remediation, not development, and the reason it leads is in §1.

---

## 0.1 Before anything

```
git status --short --untracked-files=all
python -m pytest -q
alembic current
```

Expected: clean tree apart from `pkgcheck.txt`, **541 collected, 541
passed**, head `9a4e7c1d5b82`. If any of those three differ, record what
you actually found and say whether it changes the plan. Day 9 opened by
running `git status` and found the entire Day 8 service uncommitted, so
this is not ceremony.

Two of the three were confirmed against a code snapshot before this
document was written, in a container with no database and no `.git`:

| Check | Value | How it was confirmed |
|---|---|---|
| Test suite | 541 passed | run, on the snapshot |
| Migrations | 8 files, `9a4e7c1d5b82` is the leaf | read off `down_revision`; nothing names it as a parent |
| `alembic current` | not confirmed | needs the live database |
| `git status` | not confirmed | the snapshot had no `.git` |

So `alembic current` is the one line of §0.1 nobody has yet run. If it
does not say `9a4e7c1d5b82`, the database and the migration files
disagree, and that is a stop-and-write-up, not a thing to migrate past.

## 0.2 Stop conditions

Stop and write up rather than working around, if any of these happen:

1. Any of the original 541 tests fails. That is a finding, not a chore.
2. A change would touch a row in `CLAUDE.md` §1, or anything in `docs/`
   described as deliberate, on purpose, not a bug, or by design.
3. A migration you generate would drop or alter an existing column.
   Adding a table is in scope; changing one is not.
4. You would spend more than **one** real Adzuna ingestion run.
   `adzuna_max_pages_per_run` is 2 against a ~1,000-call **monthly**
   quota, and Day 12 needs it. Verify with `--dry-run` throughout;
   spend the one real run only where Task 2 says to.
5. You need a credential you do not have.
6. Task 0 is not finished. The one real run in §2.4 spends credentials
   that are currently compromised; see §1.

Otherwise keep going. Prefer recording a gap honestly over inventing a
workaround for it.

## 0.3 Never

- Read, print, echo, `cat` or `grep` `.env`. Five credentials live
  there. CLAUDE.md §3.
- Modify `scripts/pack.ps1`. Its `$ForbiddenPatterns` already fails
  closed on `.env`, `.git/`, `storage/`, `*.zip` and `*.pdf`.
- Log a URL, request, response body or exception originating in
  `app/integrations/`. Adzuna's credentials are query parameters.
- Squash or amend commits after the fact.

---

## 1. Where things stand

Day 9 built the graph: seven nodes in `app/workflows/`, `run_agent.py`,
`build_run_summary(state) -> dict` pure and separately tested. Day 10
Parts 1 and 2 closed the three issues Day 9 carried forward — the
counter conflation, the dependency pins, the tracer guard — and are
committed as `aeddaa0`, `7ff2b0b`, `25789bd`, `fdb9c05`.

Part 3 is the rest of Day 10: **persist what the graph decided, and make
it run without a person watching.**

### Incident eleven happened between Part 2 and this document

An archive of this repository was shared that had not been built with
`pack.ps1`. It contained `.env`, the whole of `storage/cvs/` — real
candidate CV PDFs across user directories 2, 3 and 10 — and a stray
`files (1).zip`. Three of the five `$ForbiddenPatterns`, in one archive.

`pack.ps1` still needs no change. It was bypassed, which is how all ten
previous incidents happened and is the specific thing CLAUDE.md §3
warns about under "Archives".

Two consequences, and they are different in kind:

- The four credentials in CLAUDE.md §7 have now left the machine a
  **second** time, having never been rotated after incident ten. The
  clock on that human task got shorter, not longer.
- `storage/` is a category no previous incident involved. Those PDFs are
  personal data belonging to people who are not the operator of this
  repository. A key can be rotated; a leaked CV cannot be recalled.

Task 0 exists because of this, and §0.2 stop condition 6 exists because
spending the one real Adzuna run on a key you are about to rotate wastes
both the run and the rotation.

### One counter that can never fire

Established in review and belonging in your record:
`users_skipped_no_profile` can never be non-zero through
`run_agent.py`. `select_target_user_ids` draws its ids from
`Profile.user_id`, so on a full run every id in the loop has a profile
by construction, and the only way to reach that branch —
`--user-id X` for an X with no profile — terminates at
`no_scorable_users` before scoring runs. Not a bug. But a future reader
seeing `no_profile: 0` will take it as evidence that no such user
exists, when it is evidence of nothing. Document it where the counter is
defined.

---

## Task 0 — remediate incident eleven

Not development. Do this first and record it first.

### 0.a Rotate, in this order

Adzuna before Gemini, per CLAUDE.md §7 — Gemini's key travels in a
header and has never been printed, Adzuna's travels in a query string
and has now been exposed twice.

1. `ADZUNA_APP_ID` / `ADZUNA_APP_KEY`
2. `TELEGRAM_BOT_TOKEN`
3. `GEMINI_API_KEY`
4. `DATABASE_URL` — rotate the password, not the host.

This is a human task and you cannot do it. What you **can** do is state
plainly in the record whether it has been done, and refuse to run §2.4
until it has. Do not read `.env` to check. Ask.

### 0.b Prove the packing path works before it is needed again

```
powershell -File scripts/pack.ps1 -SelfTest
```

Report its output. If `-SelfTest` passes, the mechanism was never the
problem and the record should say so in those words, because the
tempting conclusion after two identical incidents is that the script is
broken.

Then run `git status --short --untracked-files=all` and decide about
every `??` line, per CLAUDE.md §3 — a valid archive that silently drops
untracked records is the second failure mode described there, and it is
not the one that just happened.

### 0.c Say what would have caught it

One paragraph, no code. The two incidents share a cause: a person
reaching for Explorer or `Compress-Archive` instead of the script. Name
the cheapest thing that would have made that fail loudly. Do not build
it — Day 10 is full. Name it, and put it in §7 as open.

---

## Task 1 — `agent_runs`

Design Note §8 chose not to build this table on Day 9, on the argument
that Day 11 moves the node set and a schema designed then would be
migrated twice. The defusal was structural:
`build_run_summary(state) -> dict` "returns exactly the fields an
`agent_runs` row would hold. Day 10's migration persists a dict that
already exists." Task 1 is cashing that in. If the claim turns out not
to hold, say so plainly — that is a more valuable finding than a
migration.

### 1.1 Derive the schema, do not design it

Read `build_run_summary()` in `app/workflows/state.py` and its tests
first. The columns come from its actual return value, not from what
seems sensible.

The snapshot returned **38 keys**. That number is given so you can
*check* rather than *discover*, and **a mismatch is itself a finding** —
report it before proceeding:

```
status, dry_run, user_id, started_at, finished_at, writes_prevented,
computation_performed, persistence_performed, stages_attempted,
stages_skipped, stages_computed, stages_persisted, errors,
terminal_reason, notify_branch, notify_eligible, users_considered,
users_with_profile, users_with_embedded_cv, ingestion_status,
ingestion_run_id, jobs_inserted, embedding_status, jobs_embedded,
embeddings_remaining_null, enrichment_status, jobs_enriched,
enrichment_remaining_null, scoring_status, scoring_run_id,
users_skipped_no_cv, users_skipped_no_profile,
users_skipped_no_active_cv, users_skipped_cv_not_embedded,
users_scored, jobs_scored, pairs_scored, jobs_skipped_no_embedding
```

Re-derive it from the function rather than trusting this block.

**Six keys have ambiguous types. Decide each explicitly and record the
reason:**

- `errors`, `stages_attempted`, `stages_skipped`, `stages_computed`,
  `stages_persisted` all come back as Python lists. `JSONB` or
  `ARRAY(Text)` is a real choice, not a formatting one: it changes how
  the §1.4 drift test compares columns, and `ARRAY` will not hold
  `errors` if an error is ever a dict rather than a string. Check what
  the error path actually puts in that list before choosing.
- `started_at` / `finished_at` arrive as whatever `finalise` stamped.
  Confirm they are timezone-aware before choosing `TIMESTAMPTZ`, and if
  they are naive, say so rather than silently coercing — `scoring_runs`
  is the comparison point.

Two rules the summary already follows and the table must not break:

- **Absent is not zero.** `jobs_enriched` and
  `enrichment_remaining_null` come back `None` after a dry run because
  the path returns before computing them. Those columns are nullable and
  stay nullable. Defaulting them to `0` is the same mistake as
  defaulting an abstained signal, which is a §1 row.
- **The summary reads no clock.** `finished_at` is stamped into state by
  `finalise` before the summary is built, which is what makes two calls
  on the same state identical. The repository writes what the summary
  hands it; it does not call `now()`.

### 1.2 The migration

New Alembic revision on head `9a4e7c1d5b82`. Additive only — a new
table, no changes to existing ones. Both `upgrade()` and `downgrade()`
implemented and both exercised: upgrade, downgrade, upgrade again, and
report `alembic current` at each step.

### 1.3 Model, repository, wiring — and one tension to resolve

Follow the shape `ScoringRunRepository` already uses
(`app/db/repositories/scoring.py`) — that is the local convention for a
run row and there is no reason for a second one.

**But read that file before copying it.** `ScoringRunRepository.start()`
writes the row *before* any work happens, and its docstring says why:
"a pass killed mid-flight leaves a row stuck at 'running' rather than no
row at all. A crash that erases its own evidence is the hardest kind to
investigate."

`build_run_summary()` does not exist until the run has finished. A
single write at the end is the obvious implementation and it produces
the exact outcome that docstring calls the hardest kind to investigate.

So you are choosing between two shapes, and §2.3 asks you what a crash
leaves behind. Do not answer that question by accident here. Decide,
state which you chose, and state what an interrupted run leaves under
your choice. **Do not add a status enum** — that is Day 11's territory
and is out of scope. Naming the consequence is in scope.

Persist **only when `dry_run` is false.** A dry run that wrote an
`agent_runs` row would contradict `writes_prevented`, which is one of
the fields it would be writing. Test that directly: run the graph dry,
assert the table is unchanged.

### 1.4 The test that stops drift

Day 11 adds a notification node and the summary grows fields. A field
added without a column would be silently dropped on write while every
existing test stayed green.

So: a test asserting **every key returned by `build_run_summary()` has a
column on `agent_runs`**, driven off the model's actual columns and the
summary's actual keys rather than a hand-maintained list. Prove it can
fail — add a throwaway key to the summary, watch it fail, remove it, and
confirm the file is byte-identical afterwards. Record that you did.

Decide and state which direction it guards: a summary key with no column
must fail. Whether a column with no summary key should also fail is your
call — argue it either way, but argue it.

### 1.5 Verification

```
python -m scripts.run_agent --dry-run     # writes nothing
python -m scripts.run_agent               # writes exactly one row
```

Predict the row's contents **before** running the second one and record
the prediction, then compare. Day 9's headline result was a prediction
that matched in every particular, and that only means something because
it was written down first.

Query the row back and show it. Confirm `recommendations` moved as
expected and that `scoring_runs` still gets its own row — `agent_runs`
records the graph's decisions, not the work, and both should exist.

---

## Task 2 — unattended operation

The graph currently runs when a person types the command. Task 2 is
making it run when nobody does, and making the result legible the next
morning.

### 2.1 What already holds

`run_agent.py` exits non-zero on `failed` or `degraded`, so a scheduler
can distinguish a problem from a quiet night without parsing output.
Quota exhaustion is `degraded`, never a quiet success. A run that
computed nothing reports `complete_no_work`. `build_graph()` refuses to
start if the environment would enable the langsmith tracer.

Verify each of those four still holds before you rely on them, and say
how you verified rather than asserting it.

### 2.2 Scheduling

This is a Windows machine (`E:\AI_JOB_HUNT_AGENT`, PowerShell, `.venv`).
Write a registration script under `scripts/` in the style of
`pack.ps1` — including a `-SelfTest` switch that proves the mechanism
without arming anything.

It must:

- Run inside the project's `.venv`, not whatever Python is on PATH.
- Set the working directory to the repo root, since `cv_storage_dir`
  resolves relative to it.
- Capture stdout, stderr **and the exit code** to a timestamped log
  under a gitignored directory. An exit code that is not recorded is an
  unattended run that reports nothing.
- Not pass `--dry-run`. A scheduled dry run is a scheduled no-op that
  looks healthy, which is §0 in its purest form.

Before writing a line to that log, apply CLAUDE.md §3: the run invokes
`app/integrations/adzuna.py`, and an unattended log is exactly the kind
of incidental secret handling that produced nine of the eleven
incidents. State what the log can contain on a failing run.

State plainly what the script does *not* handle — overlapping runs,
missed runs while the machine was off, log growth. Do not solve all
three; name them and pick, with reasons.

### 2.3 Restart and resilience

`scripts/restart_resilience_dryrun.py` and
`scripts/concurrent_claim_dryrun.py` already exist. Run them, report
their output, and say whether either covers "the process died mid-run".
If neither does, describe what an `agent_runs` row would look like in
that case — an unfinished row, or no row at all — and record which one
the write point you chose in §1.3 produces. Do not add a status enum for
it; that is Day 11's territory. Name it.

### 2.4 The one real run

**Blocked until Task 0.a is confirmed done.** Do not spend it on
credentials that are scheduled for rotation.

Spend it here. Run the scheduled task once, end to end, unattended path,
not by hand. Then show the log file, the exit code, the `agent_runs` row
and the `scoring_runs` row.

Predict all four before triggering it.

---

## Task 3 — the abstention asymmetry, separated but not decided

Day 11 attaches Telegram delivery to a branch that **has never fired on
real data**. `notify_eligible` is 0 because `weight_covered` sits at
0.50 against a floor of 0.55, and the floor is a step function with
three observed values, so lowering it to 0.45 or 0.40 changes nothing.

Design Note §10 deliberately did not decide the asymmetry: an abstaining
signal leaves the denominator while a `0.0` stays in it, so missing data
can outrank bad data. It said deciding it under time pressure is how it
gets patched instead of decided. **That reasoning still holds. Do not
change `combine()`** — `app/services/scoring.py`, and the floor it is
read against is `min_weight_covered_to_notify` in `app/core/config.py`.

What to build instead is the thing that separates the causes — CLAUDE.md
§2. A read-only script, `scripts/asymmetry_isolate.py`, that writes
nothing and answers, over today's 294 pairs:

- The distribution of `weight_covered`, with counts per observed value.
- Which signals abstain, how often, and for how many distinct jobs.
  The five nullable signal columns on `recommendations` are the source:
  `semantic_score`, `skill_score`, `experience_score`, `location_score`,
  `title_score`. **NULL is abstain** — §1 row; read them as such.
- What `weight_covered` would become under each candidate treatment of
  abstention — at minimum: as-is, abstentions kept in the denominator,
  and the floor lowered — and how many pairs would clear 0.55 under
  each.
- Which of those, if any, produces a non-zero `notify_eligible`.

Compute the candidates in the script, from the stored columns. Do not
re-run scoring to get them, and do not import `combine()` and mutate its
behaviour — a script that shares code with the thing it cross-checks is
what `test_scorable_targets_check_does_not_reference_the_shared_predicate`
exists to prevent, and the same reasoning applies here.

Report the numbers. Do **not** recommend one. The point is that the
decision arrives at Day 11 with evidence attached instead of arriving as
a guess made in a hurry, and a script that argues for an answer is worth
less than one that shows the alternatives.

If the answer is that no candidate produces a non-zero
`notify_eligible`, that is the single most important sentence in your
report. Say it first, in `docs/Day_10_progress.md`, in its own line.

---

## Records and commits

`docs/Day_10_progress.md` — written as you go. Numbers reported as
before-and-after pairs, never a delta you computed. Open with anything
that went wrong.

**Heading collision, check before you write.** That file already uses
`## Part 1.` through `## Part 4.` for the internal sections of Day 10
Parts 1 and 2 — `## Part 3.` is currently "Task 2 — the breakdown
reaches the unattended path", which is not this Part 3. Pick a heading
scheme that cannot be misread a month from now and say in one line why
you picked it.

`CLAUDE.md` §6 — new counts, two numbers. §7 — settled decisions and
open issues, including incident eleven and whatever §0.c named. §1 —
propose any new row, and say either way; do not add one silently. A
do-not-fix list that absorbs every decision stops being read.

Commits, one per task, records travelling with the change they describe:

1. Task 0 — packing verification and record. No code change expected;
   if the tree is clean, say so and skip the commit rather than
   manufacturing one.
2. `agent_runs` — migration, model, repository, wiring, drift test
3. Scheduling — registration script, gitignore entry, resilience notes
4. `asymmetry_isolate.py` — read-only, plus its findings in the record

Verify each commit's tree in isolation, the way Part 2 did: stash the
rest, run the suite against that commit alone, report the number. A
commit that only passes with later work applied is a commit that never
passed.

Report the hashes.

---

## Final report

End with a report in this shape, because a person is going to turn it
into a teaching document without the repository in front of them:

1. Collected counts at each checkpoint, plus the final pass/fail split.
2. What was built, per task, in one paragraph each.
3. **Every prediction made, and whether it held.** Both kinds are
   useful; a prediction that failed is worth more than one that held.
   The 38-key list in §1.1 counts as a prediction — say whether it
   matched.
4. Findings — anything true you did not know before, including things
   that turned out fine.
5. Decisions, with what you rejected and why. The §1.3 write-point
   choice and the §1.1 list-column choice both belong here.
6. What you could not do, and what would be needed.
7. Open after Day 10.

## Not in scope

- **`.env` rotation itself.** Human task. Task 0 confirms and blocks on
  it; it does not perform it.
- **`combine()` and the abstention asymmetry itself.** Task 3 measures;
  it does not decide.
- **A status enum for interrupted runs.** Day 11. §1.3 names the
  consequence without encoding it.
- **Telegram delivery.** Day 11. `NOTIFICATION_PATH_MAP` keeps both
  entries pointing at `finalise`; that is a §1 row.
- **A guard that makes hand-made archives fail loudly.** §0.c names it,
  Day 10 does not build it.