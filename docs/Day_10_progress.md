# Day 10 — Part 1 committed, and the two gaps it left

Part 1's three changes are in `docs/Day_9_progress.md` Part 8, which
closed the Day 9 open issues. This file records Part 2: committing that
work, and closing two gaps Part 1 left behind.

A separate file rather than more of Part 8, because Part 8 is a record
of Day 9's issues being closed and this is Day 10's own work — including
one decision that **reverses** something Part 1 recorded as deliberate.
Burying a reversal inside the record of the thing it reverses is how it
stops being findable.

Test counts, as two numbers each rather than deltas:

| Point | Collected |
|---|---|
| Part 2 baseline | 515 |
| After Task 0 (commits only) | 515 |
| After Task 1 (flag/credential split) | 535 |
| After Task 2 (skip breakdown in the summary) | **541** |

Final: **541 collected, 541 passed.**

---

## Part 1. Task 0 — three commits, and why intermediate greenness was checked

Part 1 finished green and **uncommitted**, which is the state this
repository has already lost work to once: Day 9 opened with HEAD
carrying the Day 8 migration and none of the Day 8 code.

Three commits, one per change, each carrying its own records rather than
leaving a fourth housekeeping commit:

| # | Hash | Subject | Tree green in isolation |
|---|---|---|---|
| 1 | `aeddaa0` | parse every alias of a multi-alias import | 483 passed |
| 2 | `7ff2b0b` | say which of three causes skipped a user | 494 passed |
| 3 | `25789bd` | refuse to build the graph while a tracer could listen | 515 passed |

The per-commit counts are not decoration. `tests/test_workflow_graph.py`
carries changes belonging to commits 1 **and** 3, so the file had to be
split across them: commit 1 got a parser-only version with the tracing
tests and their two imports removed, and commit 3 restored the whole
file. A split like that can easily leave commit 1 with an import of
something only commit 3 introduces — a tree that never passes and that
nobody notices, because the working tree is green the whole time. Each
commit was therefore checked by stashing the remaining work and running
the suite against that commit alone. The three numbers above are those
runs, and they match the Part 1 checkpoints exactly.

### Two corrections to the Part 1 dependency comment

1. **`truststore` is required by both `httpcore2` and `httpx2`.** The
   comment already said this — `truststore Required-by httpcore2,
   httpx2` — so nothing changed.
2. **The `starlette[full]` path is LATENT, not active.** The comment
   said the extra was "NOT installed here" but did not name the
   condition or say what would change it. It now says latent explicitly,
   and adds that installing `starlette[full]` later would make the path
   active and these three pins would then survive removing `langgraph`
   — which is the thing a future reader would otherwise conclude
   wrongly from the rest of the comment.

`pkgcheck.txt` (UTF-16 `pip show httpx` output) was present in the
working tree, is not mine, and was kept out of every commit.

---

## Part 2. Task 1 — the guard stopped firing on correct configurations

### What was wrong, and why it was worse than a quirk

Part 1's `tracing_vars_set` treated every name identically: any
non-empty value counted as set. So `LANGCHAIN_TRACING_V2=false` raised.

Part 1 recorded that as deliberate — "stricter than langchain-core" —
and that was a mistake, recorded confidently one turn earlier. The
strictness was judged as a safety property in isolation, without asking
which environments would actually meet it. `false` is the documented way
to turn tracing off. It is what appears in Compose files, CI configs and
deployment templates written by people being careful. **So the
environment most likely to trip the check was one where somebody had
explicitly disabled tracing**, and the process died claiming tracing was
enabled, which was untrue, in front of a config line saying otherwise.

That does not survive contact with a deployment. The next person under
time pressure, looking at a crash that contradicts a line they can read,
deletes the guard — and then there is no guard at all. A check that is
wrong in exactly the case it exists to approve is worse than no check,
because it spends the credibility that would have made the real signal
believed.

### The split

**Flags** — `LANGCHAIN_TRACING`, `LANGCHAIN_TRACING_V2`,
`LANGSMITH_TRACING` — are parsed for truthiness, case-insensitive, with
surrounding whitespace stripped.

**Credentials and destinations** — the API keys, endpoints and project
names — are unchanged. Presence with any non-empty value is the signal.
There is no value `LANGSMITH_API_KEY` could hold that makes it innocent
in an environment that is not tracing, so its content is not a question
worth asking.

Boundaries, each tested at the exact value rather than near it:

| Value | Result |
|---|---|
| `"false"`, `"FALSE"`, `"False"`, `" false "` | not enabled |
| `"0"`, `"off"`, `"no"` | not enabled |
| `""`, `"   "`, absent | not enabled |
| `"true"`, `"TRUE"`, `" true "`, `"1"`, `"on"`, `"yes"` | enabled |
| `"maybe"` — unrecognised | **enabled** |

### The unrecognised case, decided rather than fallen out of

An unparseable flag counts as **enabled**. Somebody set the variable
intending something and we cannot tell what, and the two errors are not
symmetric: a false positive costs a crash with a readable message naming
the variable, a false negative costs CV-derived profile text exported to
a third party with no signal at all.

`config.py` enumerates only the **disabled** values, so that direction
is structural. Enumerating the enabled ones instead — `true`, `1`,
`yes`, `on` — would send an unknown value to "not enabled" by accident
rather than by decision, and the accident would point the unsafe way.

### The secrets consequence of reading values

The flag branch is now the only place in `config.py` that reads a value
rather than testing it for emptiness. The value is consumed by
`_flag_is_enabled()` and only the NAME is appended to the result. A
version that helpfully reported which value it rejected would be the
leak — section 3 records that every incident so far came from a secret
handled incidentally while doing another job, not from printing `.env`.
There is a test that plants a secret-looking flag value and asserts it
appears nowhere in the error message.

A second test asserts the two categories are **disjoint and cover every
name**. That one exists because a credential misplaced among the flags
would be parsed for truthiness, and under the fail-closed default an
unrecognised key value still reports — so the misplacement would be
masked by the very thing that makes the design safe.

### Live re-verification, all three directions

```
LANGCHAIN_TRACING_V2=true      -> exit 1, message names LANGCHAIN_TRACING_V2
LANGCHAIN_TRACING_V2=false     -> exit 0, status dry_run, 294 pairs
LANGSMITH_API_KEY=placeholder  -> exit 1, name present, `grep -c placeholder` = 0
```

The middle one is the case that used to fail and is the reason for the
change.

---

## Part 3. Task 2 — the breakdown reaches the unattended path

Part 1 closed the invisibility hole for `scripts/score_jobs.py`, the
tool a human runs while watching. `scripts/run_agent.py` is the path
that runs unattended on Day 10 with nobody reading it, and
`build_run_summary()` reported `users_scored` without the three skip
counters. A scheduled run that silently stopped scoring somebody would
have emitted a summary indistinguishable from a healthy quiet day, in
the only artifact that run produces.

### No plumbing change was needed

`score_and_rank` already stores `run_scoring`'s whole returned dict as
`state["scoring"]`, and Part 1 added the three keys to that dict. So the
smallest change was three reads in `build_run_summary()` plus the
renderer list in `run_agent.py`. The total, `users_skipped_no_cv`, was
added alongside them — a breakdown without its total is unreadable in a
log nobody is watching, and `score_jobs.py` prints both.

**Absent stays `None`, never `0`.** A scoring stage that never ran has
no opinion about how many users were skipped, and defaulting to zero
would state one. That is the same distinction the NULL signal columns
make and the same one `jobs_enriched` already makes after a dry run —
both already on the do-not-fix list.

### Why this could not be forced live, and what was done instead

`--user-id 9999` forces the skip branch through `scripts/score_jobs.py`,
because that flag does not check the user exists. **It does not work
through `scripts/run_agent.py`**, and the reason is the graph working as
designed: `resolve_targets` reports `users_with_embedded_cv 0`,
`route_after_targets` sends the run straight to `finalise` with
`terminal_reason no_scorable_users`, and scoring never executes. Run
live it prints `scoring_status None`, `users_scored None`.

On today's real rows every user is scorable, so the breakdown is
`0/0/0` and a run against real data would pass whether or not the keys
were wired up at all. It is therefore forced with stubs: a graph
execution with a stubbed `run_scoring` returning `4 = 1 + 1 + 2`,
asserted through to the summary, and a second run that stops at target
resolution asserting all four keys come back `None` rather than `0`.

That leaves a real gap, recorded rather than papered over: **the
end-to-end path is covered by a stubbed test only**, and will stay that
way until a real user is unscorable while another is not.

---

## Part 4. Records and what stays open

`CLAUDE.md` §6 carries the new counts; §7 gains "Closed by Day 10 Part
2" and "Open after Day 10 Part 2", and the Part 1 tracing bullet now
points forward to the entry that corrects it rather than standing as
written.

**No new §1 row was added for the flag split.** It is explained where it
lives — in `config.py`'s two tuples, in the inverted test, and here —
and §1 loses force if it becomes a changelog. Two candidate rows were
left out on the same reasoning in Part 1.

Still open:

- **`.env` rotation.** `GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN`,
  `ADZUNA_APP_KEY` and `DATABASE_URL` have left the machine. Human task,
  not started, and it has a clock on it.
- **The workflow skip breakdown cannot be exercised live** — see Part 3.
- Everything Day 8 and Day 9 left open: the abstention asymmetry, the
  `||` multi-company field, the agency list, job 81's attempts ceiling.

---
---

# Day 10 Part 3 — persistence and unattended operation

Heading scheme, stated once because the collision is real: everything
below is prefixed **`Part 3, Task N`**. The word "Part" already means
two things in this file — Day 10 Parts 1 and 2 as sessions, and
`## Part 1.`–`## Part 4.` as their internal sections — so a bare
`## Part 3.` would have been the third ambiguity rather than a heading.
Task numbers come from the prompt, not from me, so they cannot drift.

Written as the work happened, not reconstructed afterwards.

## Part 3, §0.1 — the preflight found something

Two of three checks matched. One did not, and it is the reason §0.1
exists.

| Check | Expected | Found |
|---|---|---|
| Test suite | 541 collected, 541 passed | **541 collected, 541 passed** |
| `alembic current` | `9a4e7c1d5b82` | **`9a4e7c1d5b82 (head)`** |
| `git status` | clean but for `pkgcheck.txt` | **not clean** — see below |

`alembic current` was the one line of §0.1 nobody had run. It matches
the migration files: the database and the eight revisions agree, head is
`9a4e7c1d5b82`, and nothing needed to stop.

### What `git status` found

```
 D docs/Day_9_progress.md
?? docs/Day_9_Langgraph.md
?? pkgcheck.txt
```

`docs/Day_9_progress.md` had been **deleted** from the working tree, and
an untracked `docs/Day_9_Langgraph.md` had appeared beside it.

The obvious reading — a rename — is wrong. The new file is 332 lines
against the old one's 411, it is titled "Day 9 — consolidated report",
and its own third paragraph says it **"does not replace
`Day_9_Design_Note.md` … or `Day_9_progress.md`"**. So a document
arrived stating that it does not replace a file, and that file was
deleted in the same breath.

Two other files still pointed at the deleted one: `CLAUDE.md` line 235
and `docs/Day_10_progress.md` line 3 — this file, three lines from the
top. That is the same shape as Day 9's opening finding, where
`guard.md` instructed agents to read a `CLAUDE.md` that was not at the
repo root.

The deletion was uncommitted, so HEAD still had the file. Restored with
`git checkout --`, verified byte-identical to HEAD, 411 lines.
**Nothing was lost, and nothing was reconstructed** — which is only true
because §0.1 ran before any other work, and because the deletion had not
yet been committed. Had Part 3 opened with a commit, that file would
have left HEAD.

Does it change the plan? Only by adding a step: `Day_9_Langgraph.md` is
now a `??` line to decide about in §0.b rather than a mystery.

## Part 3, Task 0 — remediating incident eleven

### 0.a Rotation — asked, and the answer blocks §2.4

**Not done. None of the four credentials has been rotated.** Confirmed
by asking, not by reading `.env`.

So the four credentials named in `CLAUDE.md` §7 have now left this
machine **twice** — incident ten (an archive containing `.env`) and
incident eleven (an archive containing `.env`, the whole of
`storage/cvs/`, and a stray `files (1).zip`) — and remain live in both
copies.

§0.2 stop condition 6 therefore holds, and **§2.4 is not run.** Spending
the single remaining Adzuna run on a key scheduled for rotation would
waste the run and prove nothing about the key that will replace it.
Everything else in Task 2 is built and verified; only the real
invocation is withheld, and §2.4 below records exactly what remains
unproven because of it.

The `storage/cvs/` half has no rotation equivalent. A key can be
replaced; a candidate's CV cannot be recalled. Those PDFs belong to
three people who are not the operator of this repository.

### 0.b The packing path was never the problem

```
powershell -File scripts/pack.ps1 -SelfTest
self-test result   PASS
file count         145
byte size          447154
```

**The mechanism works. It was bypassed, not broken.** That sentence is
here in those words because the tempting conclusion after two identical
incidents is that the script is at fault, and every hour spent hardening
it is an hour not spent on the thing that actually failed. All five
patterns are enforced and fail closed: `.env`, `.git/`, `storage/`,
`*.zip`, `*.pdf`. Incident eleven's archive violated **three of the
five** — which is not a gap in the list, it is proof the list was never
consulted.

Every `??` line decided, per `CLAUDE.md` §3:

| Path | Decision | Why |
|---|---|---|
| `docs/Day_9_Langgraph.md` | **track it** | It is a project record. An untracked record is invisible to `git archive`, which is exactly how `prompts/day8_open_issues.md` was lost — the second archive failure mode §3 describes, and not the one that just happened. |
| `pkgcheck.txt` | **leave untracked** | UTF-16 `pip show httpx` output. A transient diagnostic, not a record; nothing references it and nothing would miss it. |

### 0.c What would have caught it

Both incidents were a person reaching for Explorer's "Compress to zip"
or `Compress-Archive` instead of the script, and nothing in the
repository can intercept a right-click in another application. The
cheapest thing that would have made it fail loudly is not a guard on
packing at all — it is removing the reason to pack by hand:
a `pack` entry point so obvious and so fast that the manual route is
never the shorter path, plus a single `README` line at the repo root
saying archives are built one way. The next cheapest, and the one that
actually fails loudly rather than merely competing, is a
`git`-independent check the operator runs on any zip **before sharing
it** — `scripts/verify_archive.ps1 <path>`, applying the same
`$ForbiddenPatterns` to an arbitrary file rather than to the one
`pack.ps1` just built. That inverts the protection: today the script
guarantees its own output, which does nothing about an archive it did
not produce. Named, not built — Day 10 is full. Recorded as open in
`CLAUDE.md` §7.

## Part 3, Task 1 — `agent_runs`

### The 38-key prediction held exactly

`build_run_summary()` returns **38 keys**, and they are the 38 listed in
the prompt, in that order. Re-derived from the function rather than
trusted. No mismatch — which is itself worth a line, because the whole
argument for deferring this table on Day 9 was that the summary would
still be the schema on Day 10, and it was.

`agent_runs` has **39 columns**: those 38, plus `id`.

### The six ambiguous types, decided

**The five list columns are JSONB, not ARRAY(Text).** The deciding one is
`errors`, and checking it turned up something else — see the finding
below. Four of the five hold stage names and are homogeneous strings
today, so ARRAY(Text) would fit them. `errors` has **no writer at all**,
so its `list[str]` annotation is aspirational rather than enforced, and
the first code to populate it will be an exception path written on Day 11
or later. An exception record is exactly the kind of thing that wants to
be a dict. ARRAY(Text) would make that a migration; JSONB makes it a
Tuesday. One convention for all five rather than two, so a reader never
has to remember which is which.

**`started_at` and `finished_at` are TIMESTAMPTZ.** The prompt asked
whether they are timezone-aware or naive. They are neither, quite: they
are timezone-aware ISO-8601 **strings**, because everything at a LangGraph
node boundary must be JSON-serialisable. `finalise` stamps
`datetime.now(timezone.utc).isoformat()`, and `fromisoformat()`
round-trips it exactly — verified, not assumed. So the repository parses
on the way in. Text would avoid the conversion, but `scoring_runs` uses
`DateTime(timezone=True)`, and lining the two tables up is the obvious
first question anybody asks of this one; a text column would need a cast
on every such query.

### Finding: nothing ever writes `state["errors"]`, so `failed` is unreachable

Searching for writers of `errors` found none. It is read in
`build_run_summary()`, rendered by `run_agent.py`, and populated by
nothing. `select_graph_status()` returns `STATUS_FAILED` only when
`errors` is non-empty, so **the `failed` status cannot currently occur.**

That matters beyond tidiness, because §2.1 asks me to verify that
`run_agent.py` "exits non-zero on `failed` or `degraded`". The `failed`
half of that guarantee is real code guarding an unreachable state; only
`degraded` can actually fire today. This is the same shape as the §1 row
recording that `PARTIAL` and `FAILED` never appear in `scoring_runs`
because nothing sets them — not a bug, not to be "fixed" by inventing a
writer, but not to be relied on either.

### The write point: two writes, not one

`build_run_summary()` does not exist until the run has finished, so a
single INSERT at the end is the obvious implementation. It is also
exactly what `ScoringRunRepository.start()` was written to avoid: "a pass
killed mid-flight leaves a row stuck at 'running' rather than no row at
all. A crash that erases its own evidence is the hardest kind to
investigate."

That reasoning does not stop applying because this table happens to be
filled from a dict built at the end. A workflow run is the longest thing
this project does — an enrichment pass alone is 27 to 84 minutes — and it
is about to be driven by a scheduler with nobody watching. The run most
worth having a record of is the one that did not reach the end.

**Chosen: `start()` opens a row with `started_at` and nothing else;
`finish()` fills in the summary.** An interrupted run therefore leaves **a
row with `finished_at IS NULL` and every counter NULL** — not no row.
`ix_agent_runs_unfinished` is a partial index on exactly that predicate,
so finding one is a query rather than a scan.

"Unfinished" is `finished_at IS NULL`, **not a status enum**.
`scoring_runs` draws the same distinction the same way. Naming the
consequence is in scope; encoding it is Day 11's.

### Where the persistence lives, and why that was not a choice

`scripts/run_agent.py`, around the graph invocation — never inside
`app/workflows/`. `CLAUDE.md` §1 records that `app/workflows/` imports no
repository, deliberately, "because an exception is a hole to grow into".
Persisting from a node would have been that exception. The driver owns
the database; the graph owns the decisions. This is a §1 row directly
determining a design, which is what that list is for.

### The drift test, and which direction it guards

`test_every_summary_key_has_a_column` is the one that must fail: a summary
key with no column is **silently dropped** on write, because `finish()`
copies by key and skips what it cannot store, while every other test stays
green and the run reports success.

**Proved it can fail.** Adding `throwaway_day11_field` to the summary
produced `AssertionError: summary keys with no agent_runs column:
['throwaway_day11_field']`. Removed; `state.py` verified byte-identical
afterwards.

The other direction is asserted **differently on purpose**. A column with
no summary key is not a bug — `id` is one permanently, and Day 11 may add
bookkeeping the summary does not produce. Making it fail would force the
summary to grow a key for every storage detail, inverting the dependency:
the summary is the source of truth and the table follows it. So extras are
*enumerated* rather than forbidden — `_columns() - _summary_keys() ==
{"id"}` — the same `==` rather than `<=` shape
`test_all_expected_tables_are_registered` uses.

### One of the original 541 failed, and it was right to

`test_all_expected_tables_are_registered` failed the moment `agent_runs`
was registered, because it asserts `set(Base.metadata.tables) ==
EXPECTED_TABLES` — an exact set, not a subset. That is the guard working
exactly as designed: a new table cannot appear without somebody
acknowledging it in writing. Acknowledged by adding `"agent_runs"` with a
comment saying why the assertion is `==`. Not a workaround; the
acknowledgement is the point of the test.

### Migration exercised in both directions

| Step | `alembic current` |
|---|---|
| before | `9a4e7c1d5b82` |
| `upgrade head` | `b3f7c21d9e40 (head)` |
| `downgrade -1` | `9a4e7c1d5b82` |
| `upgrade head` | `b3f7c21d9e40 (head)` |

Additive only: one new table, no existing column added, altered or
dropped. `python -m scripts.check_indexes` after the cycle reports both
HNSW indexes present — the check the Day 8 migration's docstring demands,
because `autogenerate` has twice proposed dropping them and
`show_schema.py` cannot see them.

### §1.5 verification — prediction, recorded before the run

The prompt's second command is a bare real run. **I am not running that
one**, and the substitution is deliberate: a bare real run calls Adzuna,
spending one of roughly 33 daily calls against a monthly quota, on
credentials §0.2 stop condition 6 says are compromised and awaiting
rotation. It also spends Gemini enrichment quota. So the real run is
`--skip-ingestion --skip-enrichment`, which persists an `agent_runs` row
and exercises every line of the new code while spending neither.

**Predicted, before running:**

| Field | Prediction |
|---|---|
| `--dry-run` rows in `agent_runs` | 0, unchanged |
| real run rows in `agent_runs` | exactly 1, `finished_at` NOT NULL |
| `status` | `complete_no_qualifying` |
| `stages_skipped` | `discover_jobs: skip_ingestion`, `enrich_jobs: skip_enrichment` |
| `stages_computed` | `score_and_rank` only — embedding has nothing to do |
| `computation_performed` / `persistence_performed` | true / true |
| `users_scored` / `jobs_scored` / `pairs_scored` | 3 / 98 / 294 |
| `notify_branch` / `notify_eligible` | `no_qualifying` / 0 |
| exit code | 0 |
| `scoring_runs` | one new row, separate from `agent_runs` |

### The real run crashed, and that is the most useful thing in this record

The first non-dry run since Day 10 Part 1 failed inside `score_and_rank`:

```
sqlalchemy.exc.CompileError: Unconsumed column names:
  users_skipped_no_profile, users_skipped_no_active_cv,
  users_skipped_cv_not_embedded
During task with name 'score_and_rank'
```

**Part 1 added those three keys to the dict `run_scoring` persists, but
`scoring_runs` has no columns for them.** `ScoringRunRepository.finish()`
compiles that dict straight into `update(ScoringRun).values(**counters)`,
so a key without a column is not ignored — it raises.

Why it survived Part 1, Part 2, and a commit: the test suite has **no
database**, and every live check since had been `--dry-run`, which
returns before the write. A change that passed 541 tests, passed a live
dry run, and was reviewed twice was broken on the one path that writes,
and nothing in the process could have caught it. That is the single
most transferable finding of Part 3.

### What the crash left behind — the write-point choice, vindicated on its first real use

| Table | Row | State |
|---|---|---|
| `agent_runs` | 1 | `started_at` set, `finished_at NULL`, every other column NULL |
| `scoring_runs` | 3 | `status='running'`, `finished_at NULL` |
| `recommendations` | — | 98 → 294; scoring had committed per user before the crash |

A single write at the end — the obvious implementation — would have left
**no `agent_runs` row at all**, and the only evidence that a run had been
attempted would have been a stack trace on a terminal. Instead both
run-row tables recorded the same incident at two layers, and the
`ix_agent_runs_unfinished` partial index finds it in one query. The
argument for the two-write shape was written before the crash happened;
the crash then demonstrated it within the hour.

`scoring_runs` row 3 is left as it stands. It is the record of a failed
run, and tidying it away would destroy the evidence this section is
about.

### The fix, and the one deliberately not made

Removed the three keys from the **persisted** dict only. The returned
dict, `scripts/score_jobs.py`'s printing and `agent_runs` all keep them.

The breakdown is therefore still persisted — in `agent_runs`, which has
all three columns and a drift test holding them in step with the
summary. It is simply no longer written to a table that has nowhere to
put it.

The alternative — adding three columns to `scoring_runs` — was **not**
taken, and the reason is scope, not preference: §0.2 stop condition 3
puts changing an existing table out of bounds for this session. It is a
defensible thing to want; `scoring_runs` is the scoring funnel's own
record and the breakdown arguably belongs there too. **Recorded as open
for whoever decides it**, with the note that it would then be a second
copy of numbers `agent_runs` already holds, in a table whose funnel
assertion does not cover them.

### §1.5 verification — every prediction held

Predictions were recorded above before either run. One adjustment was
stated before the retry rather than after: `agent_runs` would hold
**two** rows, not one, because the crashed run's row legitimately stays.

| Predicted | Actual |
|---|---|
| `--dry-run` writes nothing | `agent_runs` 0→0, `scoring_runs` 2→2, `recommendations` 98→98 |
| `status` | `complete_no_qualifying` |
| `stages_skipped` | `discover_jobs: skip_ingestion`, `enrich_jobs: skip_enrichment` |
| `stages_computed` | `score_and_rank` only |
| computation / persistence | `True` / `True` |
| users / jobs / pairs | 3 / 98 / 294 |
| `notify_branch`, `notify_eligible` | `no_qualifying`, 0 |
| exit code | 0 |
| `scoring_runs` gets its own row | id 4, separate from `agent_runs` id 2 |

The stored row also confirms two rules survived the round trip that a
schema can quietly break:

- **JSONB round-trips as Python lists.** `stages_skipped` reads back as
  `['discover_jobs: skip_ingestion', 'enrich_jobs: skip_enrichment']`,
  not as a string.
- **Absent is still not zero.** `jobs_enriched` and `ingestion_status`
  are `NULL` in the stored row, because those stages were skipped —
  exactly as they are `None` in the summary. A server default of 0 would
  have made a skipped stage indistinguishable from one that ran and
  found nothing.

`agent_runs` id 2 records `scoring_run_id 4`, so the graph's decision
row and the work's own row both exist and point at each other. That was
the design intent: `agent_runs` records what the graph decided, not what
the work did.

## Part 3, Task 2 — unattended operation

### §2.1 — the four claims, each verified by a stated method

| Claim | How verified | Result |
|---|---|---|
| exits non-zero on `failed` or `degraded` | read the line: `return 1 if summary["status"] in ("failed", "degraded") else 0` | holds — **with a caveat below** |
| quota exhaustion is `degraded`, never quiet success | 5 existing tests in `test_workflow_state.py` | holds |
| a run that computed nothing reports `complete_no_work` | same tests | holds |
| `build_graph()` refuses if the tracer would be enabled | live: `LANGSMITH_TRACING=1 python -m scripts.run_agent --dry-run` | exit 1, names the variable |

**The caveat matters.** `failed` is real code guarding a state nothing
can currently produce, because nothing writes `state["errors"]` (see the
Task 1 finding). So of the two statuses that exit non-zero, only
`degraded` can actually fire. The scheduler's ability to distinguish a
problem from a quiet night is real but narrower than the sentence
suggests. Not a bug — the same shape as `PARTIAL`/`FAILED` never
appearing in `scoring_runs` — but not something to lean on.

The tracer check also confirmed the older `LANGSMITH_TRACING` spelling
works live, not just the one used in Part 2's verification.

### §2.2 — the registration script

`scripts/schedule_agent.ps1`, in the style of `pack.ps1`, with a
`-SelfTest` that arms nothing. Self-test **PASS**.

The three things it exists to get right, and one of them was proven
necessary during this very session:

1. **The interpreter.** A scheduled task inherits the SYSTEM PATH, so
   `python` resolves to whatever is installed machine-wide. Partway
   through this session that happened for real: `python` became
   `C:\Python312\python.exe`, and the test suite went from 541 passed to
   **22 collection errors** — every third-party import gone at once. It
   looked like a broken repository and was a broken PATH. The task
   therefore invokes `.venv\Scripts\python.exe` by absolute path, and
   `-SelfTest` asks that interpreter for its own `sys.executable` and
   refuses if it is not the expected one.
2. **The working directory.** Scheduled tasks start in
   `C:\Windows\System32`. `cv_storage_dir` is `"storage/cvs"` — a
   RELATIVE path — so a run started there would resolve CV storage to
   `C:\Windows\System32\storage\cvs`. Hence `-WorkingDirectory`.
3. **The exit code.** Captured along with stdout and stderr, and written
   as the **last line** of the log. An unattended run whose exit code is
   not recorded reports nothing, which is worse than not running,
   because it looks like it worked.

**No `--dry-run`.** A scheduled dry run is a scheduled no-op that
reports a healthy status every morning while doing nothing.

A generated `scripts/run_nightly.ps1` holds the actual command rather
than a long `-Argument` string, because a Scheduled Task argument is
quoted by the scheduler, re-parsed by PowerShell, and parsed again by
`python -m`; each layer can lose a quote. It is committed even though it
is generated, because it is what the machine actually executes and an
auditor should be able to read it without running a generator. It is
rewritten on every register, so it cannot drift.

### What the log can contain on a failing run — checked, not assumed

The run invokes `app/integrations/adzuna.py`, and Adzuna's `app_id` and
`app_key` travel as **query parameters**, so any exception whose text
contains the request URL contains both credentials. An unattended log is
exactly the incidental-handling shape that produced nine of the eleven
incidents.

They do not reach the log, **by construction rather than by luck**:
`AdzunaClient` passes every provider error through
`describe_http_error()` and raises `from None` specifically so a chained
traceback cannot print the original URL, and the ingestion node stores
that already-redacted string instead of formatting an exception of its
own. So a failing run's log can contain a redacted provider message, a
traceback whose frames are this repository's own files, and the summary,
which prints counters and status strings only.

That is a claim about today's code, not a property of logging. If a
future change logs a raw exception from `app/integrations/`, this log
becomes the leak. `logs/` is gitignored for that reason — and repairing
`.gitignore` was itself a small incident: the file had no trailing
newline, so appending produced `*.ziplogs/`, silently un-ignoring `*.zip`.
Caught by checking `git check-ignore` afterwards rather than trusting
the append.

### Not handled, named rather than quietly omitted

- **Overlapping runs — handled**, via `-MultipleInstances IgnoreNew`.
  Chosen over `Queue` because two concurrent runs would both score the
  same rows and the second would spend Adzuna quota re-fetching what the
  first is inserting. Dropping is the cheaper wrong answer, and
  `agent_runs` makes the drop visible afterwards: one row where two were
  expected.
- **Missed runs — partly.** `-StartWhenAvailable` covers a machine
  asleep at 03:00. It does not cover a machine off for a week: Windows
  fires one catch-up run, not seven, and nothing distinguishes "ran
  once, late" from "ran once, on time" except `agent_runs.started_at`.
  Doing better needs schedule-vs-actual reconciliation, which belongs
  with Day 11's status work.
- **Log growth — not handled.** One ~3 KB file per run, about 1 MB a
  year. Last on the list rather than solved because a rotation scheme is
  code that can delete the evidence of the run you most wanted to read,
  and 1 MB/year does not buy that risk.

### §2.3 — restart and resilience

Both existing scripts were run. Neither needed a code change; both
needed a fix at the invocation, which is worth recording because the
next person will hit the same thing:

- `restart_resilience_dryrun` crashed on `UnicodeEncodeError: 'charmap'
  codec can't encode '\U0001f44b'` — the console is cp1252 and a bot
  reply contains an emoji. Re-run with `PYTHONIOENCODING=utf-8` it
  **passes**: after disposing and re-initialising the engine, `/start`
  resumed at `AWAITING_ROLES` and the stored CV survived.
- `concurrent_claim_dryrun` requires `--user-id`.

**Neither covers "the process died mid-run" of the workflow.** The first
covers onboarding state across a process restart; the second covers two
extractions racing for one CV.

That question was answered empirically instead, by accident, earlier in
this session: the crashed real run left **`agent_runs` id 1 with
`finished_at NULL` and every other column NULL**, and `scoring_runs` id
3 stuck at `status='running'`. Both tables recorded the same incident at
two layers. That is what an interrupted run leaves under the write point
chosen in §1.3, demonstrated rather than described.

### A caution: `concurrent_claim_dryrun` is not a dry run

Despite the name it fires **real Gemini extractions**. Running it made
two live calls, one of which timed out, and it left **CV 19 in
`extraction_status = 'extracting'`**. `cv_versions` did not grow (9
before, 9 after), so no data was corrupted, but a CV parked in
`extracting` may be skipped by future extraction claims.

Not silently repaired: I cannot tell from here whether that row was
already stuck before, and resetting an extraction status is a data
mutation nobody authorised. **Flagged for a decision.** The script's
name is the finding — "dryrun" in this repository means "no writes" for
`scoring_isolate` and `notify_reachability_probe`, and means something
else here.

### §2.4 — not run, and why

**Blocked by §0.2 stop condition 6.** Task 0.a confirmed no credential
has been rotated after incident eleven, so spending the one remaining
Adzuna run would spend it on a key that is compromised and scheduled for
replacement — wasting both the run and the rotation.

What that leaves unproven, stated plainly: the scheduled task has never
been triggered by the Task Scheduler. `-SelfTest` proves the
interpreter, the working directory, the log directory and the command
line; it does not prove Windows will start the task at 03:00, that the
task's credentials can read the repo, or that the log lands with a real
exit code in it. Everything up to the moment Windows takes over is
verified; the handover itself is not.

---

## Part 3, Task 3 — the abstention asymmetry, measured

### The headline, first, in its own line

**Removing the asymmetry makes notification strictly LESS reachable, not
more.** Candidate B — abstaining signals kept in the denominator at 0.0
— produces `notify_eligible = 0` at **every** coverage floor down to
0.30, because it halves the score range: candidate A's `final_score`
tops out at **0.9835**, candidate B's at **0.4917**, against a threshold
of 0.7 that neither floor nor coverage affects.

So the asymmetry is currently the **only** reason any pair is anywhere
near the notification threshold. That is the opposite of what "missing
data can outrank bad data" suggests at first reading, and it is the fact
Day 11 most needs before it decides anything.

### The script

`scripts/asymmetry_isolate.py`. No writes, no API calls. `combine()` is
**not imported** — importing it and mutating its behaviour to try each
candidate would make the script share code with the thing it is
cross-checking, and a candidate evaluated by the code that produced the
problem cannot disagree with it.

The arithmetic is reimplemented from `app/services/scoring.py`'s formula
and is **self-checking**: candidate A must reproduce the stored
`final_score` from the stored signal columns before any other candidate
is printed.

```
rows compared      : 294
mismatches         : 0
worst difference   : 1.110e-16
```

Machine epsilon. The reimplementation is right, so the candidates are
computed on the same basis as the rows.

### What abstains

294 pairs, 98 jobs, 3 users, `scoring_run_id 4`.

`weight_covered` distribution: **0.35 ×46, 0.50 ×242, 0.80 ×6** — still
three values, the step function the Day 9 probe found, now over 294 rows
instead of 98.

| signal | weight | pairs abstained | distinct jobs | % of pairs |
|---|---|---|---|---|
| semantic | 0.20 | 0 | 0 | 0.0% |
| skill | 0.30 | 288 | 96 | 98.0% |

(experience, location and title follow in the script's output.)

### The candidates

| candidate | floor | pass_final | pass_sem | pass_cov | **eligible** |
|---|---|---|---|---|---|
| A as-is | 0.55 ← current | 3 | 33 | 6 | **0** |
| A as-is | 0.50 | 3 | 33 | 248 | **2** |
| A as-is | 0.45 / 0.40 | 3 | 33 | 248 | **2** |
| A as-is | 0.35 / 0.30 | 3 | 33 | 294 | **2** |
| B abstain in denominator | any of 0.55 … 0.30 | **0** | 33 | 294 | **0** |

Under B the coverage gate can never bind — coverage is 1.0 by
construction — so the whole problem moves onto `final_score`, and no row
clears 0.7.

Rows failing exactly one gate, candidate A: at 0.55, **2** blocked by
coverage alone; at 0.50 and below, **28** blocked by `final_score` alone
and **1** by semantic alone. Consistent with the Day 9 probe's finding
that final score is materially more constraining than semantic once
coverage stops binding, now at 3× the sample.

### No recommendation

Deliberately none, and the script prints none. Design Note §10's
reasoning holds: deciding this under time pressure is how it gets
patched instead of decided. What Day 11 now has is the shape of the
trade-off — that the asymmetry is load-bearing for reachability, that
candidate B needs the *threshold* moved and not the floor, and that 2 is
the most any coverage change alone can produce on this data.
