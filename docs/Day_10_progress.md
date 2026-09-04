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

### FINDING: notification is reachable today by changing one config value

Reported late and in the wrong place. Part 3 gave this number only as the
reason a conditional headline sentence was correctly absent, which is true
and beside the point. Stated properly:

**`notify_eligible` goes from 0 to 2 by changing
`min_weight_covered_to_notify` from 0.55 to 0.50. Nothing else moves. No
code, no migration, no re-scoring.**

The floor was NOT changed. This is the evidence for a Day 11 decision.

#### Which claim this falsifies, and which it confirms

I cannot quote the Day 10 prompt: it does not exist in this repository
and never has (see the handoff review, Q1). So the claim is cited where
it provably lives — CLAUDE.md §1, verbatim:

> coverage floors 0.45 and 0.40 give the same result as 0.50 | the grid
> is broken | `weight_covered` has three observed values, so the floor is
> a step function, not a dial (Day 9 note §9.2)

**That row is correct and this data confirms it.** 0.50, 0.45, 0.40 and
0.30 all yield 2. The floor is a step function. Nothing there is wrong.

What is falsified is the **operational conclusion** that has been drawn
from it — that coverage is therefore a dead end not worth touching. The
step function has three steps, and the current setting sits **immediately
above the largest one**:

| `weight_covered` | pairs | position relative to the 0.55 floor |
|---|---|---|
| 0.35 | 46 | below |
| **0.50** | **242** | **below, by 0.05** |
| 0.80 | 6 | above |

242 of 294 pairs — 82% — sit at exactly 0.50, five hundredths under the
gate. "Not a dial" was read as "not movable". It is movable; it has one
move, and that move is the only one on the board.

The companion §1 row is also correct and also under-read:

> `notify_eligible = 0` | the gate is broken | the gate is working:
> `weight_covered` 0.50 < `min_weight_covered_to_notify` 0.55

Both rows are accurate. Together they have been functioning as a reason
to stop looking. **A row that explains why a number is zero is not a row
that says the number must stay zero.**

#### The two pairs, since Day 11 will send them to a person

Nobody had looked at them. Both belong to **user 2** — the same user whose
active CV 24 the `concurrent_claim_dryrun` script downgraded to `failed`.

| user | job | title | company | location | `final_score` | `semantic_raw` | `weight_covered` | rank |
|---|---|---|---|---|---|---|---|---|
| 2 | 47 | Applied AI Solutions Architect | Top Gen AI Jobs | Bangalore, Karnataka | 0.9835 | 0.6917 | 0.50 | 1 |
| 2 | 19 | Assistant Manager - Python Development | BNP Paribas | Bangalore, Karnataka | 0.8627 | 0.6314 | 0.50 | 2 |

Both sit at `weight_covered` exactly 0.50 — **on** the proposed floor, not
above it. The gate is `>=`, so they qualify. This is the boundary case
CLAUDE.md §2 insists on asking about, and it is not hypothetical here: it
is the entire result. A floor written with `<` would produce 0.

#### Why those scores are high, which is the part that should worry Day 11

Identical signal shape on both. Skill and experience abstain; semantic,
location and title carry everything:

```
job 47: semantic 0.9587  location 1.0  title 1.0   skill NULL  experience NULL
job 19: semantic 0.6568  location 1.0  title 1.0   skill NULL  experience NULL
```

`match_reasons` on both records the abstentions in words: "abstained: job
lists no extractable skills", "abstained: job states no experience
requirement".

With skill (0.30) and experience (0.20) out of the denominator, the
renormalisation reduces to a straight line. Verified against the stored
values, difference **0.000e+00** on both rows — not approximately, exactly:

```
final = (0.20*semantic + 0.15*1.0 + 0.15*1.0) / 0.50
      = 0.4 * semantic + 0.6
```

Read the constant. **For any job where skill and experience abstain and
location and title both hit 1.0, `final_score` cannot fall below 0.60
however bad the semantic match is**, and it clears the 0.7 notification
threshold as soon as `semantic_score >= 0.25`.

That is the abstention asymmetry stated as a number instead of a worry.
CLAUDE.md §7 says it "needs a decision, not a patch" and that the effect
was verified by hand; this is the closed form. The two pairs about to be
notified are not two strong matches — they are two jobs that abstained on
half the model's weight and were rewarded with a 0.6 floor for it. Job 19
in particular scores 0.8627 on a semantic similarity of 0.6314, which is
0.0114 above the semantic floor of 0.62.

**None of that argues for or against lowering the floor.** It argues that
the floor and the threshold cannot be decided separately, because
lowering the floor to 0.50 admits exactly the population whose scores are
inflated by the mechanism the floor exists to guard against.

---

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

---

# Day 10 Part 3 — handoff review: four questions, and two corrections to this record

Not new development. Four questions asked after Part 3 was reported,
answered against the database and the source rather than against this
file — which is how two of the answers turned out to be corrections
*to* this file.

## Q1 — the amendment did not arrive

**Plainly: I never received `prompts/day10_part3_amendment.md`, and I
never received `prompts/day10_part3.md` either.**

Checked, not assumed:

| Check | Result |
|---|---|
| `ls prompts/` | two files, both Day 8 |
| `git log --all --oneline -- prompts/` | one commit, `7cfdbae`, carrying those two Day 8 files |
| `git log --all --diff-filter=A --name-only` grepped for `day10`/`day_10` | `docs/Day_10_progress.md` only |

So no Day 10 prompt has ever existed in this repository's history under
any branch. This is a handoff finding, not a Part 3 error: there was no
commit 0 to make, because there was no amendment to fold in. The absence
is the same shape as `prompts/day8_open_issues.md` — CLAUDE.md §1 already
records that the original do-not-fix list was lost the same way. **Prompt
files in this project are not durable, and that has now cost the record
twice.** Committing them is the fix; it is not in this session's scope.

What follows is therefore the scheduler decision written retroactively.
It is reconstruction of an argument that was acted on but never stated,
and it should be read as weaker evidence than a decision recorded at the
time.

### Why Windows Task Scheduler over APScheduler

The plan spreadsheet's Day 10 row says APScheduler. Three docstrings say
the same — `scripts/run_agent.py:10`, `scripts/ingest_jobs.py:9`,
`app/services/job_ingestion.py:565` — all three written *before* Day 10,
as forward-references. `app/schedulers/` is reserved and empty;
`requirements.txt` has no APScheduler pin and never did.

**The decisive argument is the one §2.1 is built on: APScheduler has no
exit code.**

APScheduler is an *in-process* scheduler. It needs a long-lived Python
process that owns the schedule, and nothing in this repository runs one.
There is no daemon and no service loop; the Telegram bot is the only
long-lived process, and hanging the nightly match on it would couple job
scoring to bot uptime — a bot restart would silently skip a night.

Under Task Scheduler, `run_agent.py`'s

```python
return 1 if summary["status"] in ("failed", "degraded") else 0
```

becomes a real process exit code. `run_nightly.ps1` captures it as
`$LASTEXITCODE` and writes it as the log's last line, and Windows records
it as the task's Last Run Result. Two independent readers of one value.

Under APScheduler that property does not exist and would have to be
rebuilt:

- The process does not exit, so there is no exit code to read.
- A *returned* value is **discarded** by APScheduler. `degraded` is a
  returned value. It would vanish.
- A raised exception becomes an `EVENT_JOB_ERROR` on APScheduler's own
  event bus, reachable only by registering a listener — and `failed` /
  `degraded` are not exceptions, so the listener would not fire for them
  either.

So §2.1's first row — "exits non-zero on `failed` or `degraded`, verified
by reading the line" — would have become "emits a health signal of our own
design, verified by a test of our own design." That is not the same
property. It is the property replaced by a re-implementation of itself,
which is the shape CLAUDE.md §0 warns about: a status we invented,
reporting on a run we invented the reporting for.

Three supporting reasons, none sufficient alone:

1. **Zero new dependencies.** The langgraph precedent is fresh and
   recorded in `requirements.txt` itself: one dependency pulled in
   `langsmith`, which pulled in `httpx2`, `httpcore2` and `truststore`,
   and a telemetry client that needed `assert_tracing_disabled()` written
   to fail closed against it. Task Scheduler adds nothing to install.
2. **It surfaces exactly the failures §2.2 is about.** Interpreter and
   working directory are properties of *how the process is started*. A
   process scheduler makes them explicit and checkable; an in-process
   scheduler inherits whatever launched the daemon and hides them. The
   PATH failure that actually happened mid-session — `python` becoming
   `C:\Python312\python.exe` and the suite going to 22 collection errors
   — is that failure, and under APScheduler it would have been baked into
   a daemon nobody restarts.
3. **Overlap and catch-up already exist.** `-MultipleInstances IgnoreNew`
   and `-StartWhenAvailable` are declarative. APScheduler's equivalents
   (`max_instances`, `misfire_grace_time`, `coalesce`) are code that must
   be written, tested and kept correct.

**The counter-argument, stated rather than buried:** this ties unattended
operation to Windows. That is a real cost and it is not small. Two things
bound it. Everything platform-specific is in two `.ps1` files and nothing
else. And the docstrings' actual claim — that Day 10 would be *registering*
an entry point rather than dismantling a script — **held**. `run_agent.py`
was not touched for scheduling. If APScheduler is wanted later, it is one
module in `app/schedulers/` calling the same `run_agent` seam, and this
decision does not block it.

### Plan gap or plan non-compliance — non-compliance, and the distinction matters

**This is non-compliance.** Day 9 decision 2 is the precedent for *how* to
record it, and it is also the contrast that shows why this is the other
kind.

Day 9 decision 2 was a plan **gap**: `embed_jobs` was absent from the
plan's Day 9 row, the code added it anyway, and a test now walks every
path to prove it cannot be dropped. The plan was *incomplete*. The code
did something the plan had not thought about, and nothing in the plan was
contradicted.

Here the plan named a specific mechanism and the code used a different
one. The plan is not incomplete — it is **contradicted**. That is a
stronger claim and it needs the argument above to carry it, which is
precisely why writing the argument down late is worse than writing it
down on time.

The three APScheduler docstrings are now **stale forward-references**:
promises about Day 10 made on Day 6 and Day 9 that Day 10 did not keep.
They should be amended in Day 11 to say what happened and why — and
amended, not deleted. Silently editing them would erase the evidence
that the plan said something else, and this section would then be the
only trace of a decision that reversed the plan.

`app/schedulers/` stays empty and reserved. `CODEBASE_GUIDE.md:137-139`
already reserves it; an empty reserved directory is not dead code.

## Q2 — Adzuna spend: zero calls, and the ledger cannot prove a general answer

**Both non-dry runs on 2026-09-04 spent zero Adzuna calls.** Neither was
an ingestion run. §1.5 above records the substitution before the fact:
both were `--skip-ingestion --skip-enrichment`.

Two independent tables agree, and neither required arithmetic:

| Evidence | Observation |
|---|---|
| `ingestion_runs` — all rows | ids 1-4, `started_at` all **2026-08-31**; none on 2026-09-04 |
| `agent_runs` — all rows | ids 1-2, `started_at` both **2026-09-04**, `ingestion_status` **NULL** on both |

`ingestion_status` NULL is the positive evidence, not merely the absence
of a row: the `discover_jobs` stage was skipped, so it recorded no
opinion. Absent, not zero — the same rule as `jobs_enriched`.

**Stop condition 4's one permitted real ingestion run is unspent.**

### How the count was made, and what it cannot see

`ingestion_runs.pages_fetched` is the ledger. One page is one HTTP GET:
`AdzunaClient.search()` issues a single `self._client.get(url, params=...)`
(`app/integrations/adzuna.py:194`) and no retry transport is configured on
the client, so there is no hidden multiplication.

Per-run, as separate numbers rather than a total I computed:

| run | started_at | status | pages_fetched |
|---|---|---|---|
| 1 | 2026-08-31 16:47 | complete | 2 |
| 2 | 2026-08-31 16:51 | complete | 2 |
| 3 | 2026-08-31 16:51 | no_results | 1 |
| 4 | 2026-08-31 16:57 | complete | 2 |

**Two reasons not to read this column as a spend meter.**

First, `counters.pages_fetched += 1` runs *after* a successful `search()`
(`app/services/job_ingestion.py:279`); the increment sits below the
`except JobSourceQuotaError` / `except JobSourceError` handlers. **A call
that fails is spent and not counted.** For these four runs the column is
exact, because all four terminated `complete` or `no_results` — no
`quota_exceeded`, no `source_error`. In general it undercounts, and it
undercounts hardest in exactly the situation where you most want the
number.

Second, and worse: **the ledger is structurally incomplete.**
`docs/Day_6_JobIngestion.md:786` records "Six spent on probes, about ten
on ingestion runs" against "roughly 1,000" monthly. Probe calls are made
outside `run_ingestion` and therefore appear in **no table at all**. Day 6
also says "about ten" where the table shows four runs totalling seven
pages. Two figures from this project's own records that do not reconcile;
recorded as unfinished checking rather than averaged into one number.

### What remains for Day 12 — and a coupling nobody has named

Recorded monthly allowance: **roughly 1,000** (Day 6). That figure is
itself an approximation and has never been checked against Adzuna's
dashboard. Recorded spend: **7 pages across 4 ingestion runs**, plus
**~6 probe calls no table records**.

So Day 12's headroom is large, but the denominator is unverified and the
numerator is known-incomplete. The only authority is Adzuna's own
dashboard — which requires logging in with credentials that have leaked
twice and have not been rotated.

**Credential rotation now blocks knowing the quota, not just spending
it.** That is a new consequence of incident eleven and it is not recorded
anywhere above. It belongs in §7 next to the rotation item.

## Q3 — CV 19: the record here is wrong, and the right repair is none

§2.3's caution above makes two claims. The first is false and the second
names the wrong row. The original text hedged correctly — "I cannot tell
from here whether that row was already stuck before" — and the timestamps
answer it.

### Correction 1 — CV 19 was already stuck, five days earlier

```
cvs.id = 19 | user 2 | extraction_status 'extracting'
  created_at   2026-08-30 05:11:03
  updated_at   2026-08-30 05:43:28
  extracted_at NULL
```

Nothing has written that row since **2026-08-30**. The Part 3 session ran
on **2026-09-04**. `concurrent_claim_dryrun.py` did not put CV 19 into
`extracting` and did not touch it. Its `updated_at` is shared to the
microsecond by `cvs` 11-21, which is a bulk supersede, not an extraction.

### Correction 2 — the row it actually touched is CV 24, and that is worse

```
cvs.id = 24 | user 2 | is_active TRUE | extraction_status 'failed'
  created_at   2026-08-30 06:14:11
  updated_at   2026-09-04 04:04:37   <- the Part 3 session
  extracted_at 2026-09-04 04:04:39
  error        "Gemini request failed: Request timed out."
```

CV 24 had already extracted successfully: `cv_versions` id 10 (`cv_id`
24, version 1) was created 2026-08-30 06:14:36 and embedded on
2026-09-01. So the script **re-extracted an already-complete, active CV**,
the live call timed out, and the row now reads `failed` while its
extracted version exists and is embedded.

The script did not strand a dormant row. It **downgraded the status of a
live one.** No data was lost — `cv_versions` id 10 is intact — which is
exactly why nothing complained: scoring gates on `cv_versions.embedding`,
not on `cvs.extraction_status`, so the 2026-09-04 run still scored 3
users with a status column that had started lying. **A status that is
wrong while every current consumer looks elsewhere is CLAUDE.md §0 in
miniature.** The consumer that does read it is `app/services/profile_view.py`
— that is, what the user sees about their own CV.

**A third figure that does not reconcile:** §2.3 states `cv_versions` was
"9 before, 9 after". The table holds **12** rows. I cannot reproduce 9 as
a count of that table and do not know what was counted. Not resolved, and
not quietly dropped.

### Proposal for CV 19 — do nothing, and add a way to see it

**No UPDATE, no repair script, no migration.** The mechanism built for
this already works.

`CVRepository.claim_for_extraction()` (`app/db/repositories/cv.py:105-121`)
takes the claim when

```
extraction_status != 'extracting'  OR  updated_at < now - stale_after
```

`DEFAULT_STALE_AFTER` is **15 minutes** (`app/services/cv_extraction.py:80`).
CV 19's claim is roughly **five days** old — stale by a factor of about
480. **CV 19 is freely claimable right now.** §2.3's worry that it "may be
skipped by future extraction claims" is unfounded; the staleness window
is precisely the thing that stops a crashed process stranding a CV
forever, and it is working.

Writing an `UPDATE` here would mutate production data to fix a problem
that does not exist, and would destroy the evidence that the window
works.

What is worth building instead is **read-only**: a check that prints every
`cvs` row at `extracting` alongside its claim age, so that a *genuinely*
stuck claim — one younger than `stale_after` whose process is dead — is
visible as a number rather than inferred. `scripts/scorable_targets_check.py`
is the natural home; it already exists to make invisible states countable.

**CV 24: also do not touch it.** Its `failed` status is *accurate* about
the last attempt actually made. Re-running extraction would spend Gemini
quota to repair a cosmetic status while the useful artifact is already
correct. It needs a decision about what the profile view should say, not
a write. Named in §7.

### What should happen to the script — all three, not one of three

1. **Rename: yes, and it is the load-bearing action.**
   `concurrent_claim_dryrun.py` → `concurrent_claim_probe.py`. "dryrun" is
   not loose naming here, it is a **convention with several other
   instances** — `offline_extraction_dryrun`, `onboarding_dryrun`,
   `onboarding_edgecases_dryrun`, `restart_resilience_dryrun`, and the
   `--dry-run` flag itself — and in every one of them it means no writes.
   A name that breaks a convention is worse than having no convention,
   because the next person reads the name instead of the file. That is
   what happened, and it cost two Gemini calls and a downgraded status on
   a live user's CV. `_probe` already has precedent in
   `notify_reachability_probe`. This is a code change, so: Day 11.
2. **A §7 open item: yes.** The rename does not undo CV 24, and §7 is
   where "measured, not decided" lives.
3. **A CLAUDE.md §1 row: no** — and this is the first time in this project
   the answer to "does this belong in §1?" has been no, so it is worth
   saying why. §1 is for things that *look* like bugs and are decisions.
   This is not a decision. It is a defect with a real victim row. A §1 row
   would immunise it against being fixed, which is the exact opposite of
   what it needs.

What *does* deserve writing down as a rule is the convention the script
broke: **"dryrun" in a script name is a promise of no writes.** With that
written, the rename is enforcement of a stated rule rather than a matter
of taste.

## Q4 — Task 3's headline: the required sentence is correctly absent

### The three treatments, together

Task 3 above reports candidate B in its headline and the rest in a table.
Restated as one comparison, since the question is whether they were
reported at all:

| treatment | floor | notify_eligible |
|---|---|---|
| A, as-is | **0.55** (current) | **0** |
| A, lowered floor | 0.50 | **2** |
| A, lowered floor | 0.45 / 0.40 | 2 |
| A, lowered floor | 0.35 / 0.30 | 2 |
| B, abstentions in denominator | 0.55 … 0.30 | **0** |

As-is and the lowered floor were both measured and both are in the table
above; only B was in the headline.

### Is the required sentence in the file, and is it first? No, and no — because its precondition did not hold

The requirement was **conditional**: the sentence goes first *if no
candidate treatment produces a non-zero `notify_eligible`*. Candidate A
at floor 0.50 produces **2**. The condition is false, so the sentence is
correctly absent.

This was not a judgement call made in prose. `scripts/asymmetry_isolate.py`
implements the branch (lines 286-291): it prints
`NO CANDIDATE PRODUCES A NON-ZERO notify_eligible` only when the non-zero
set is empty, and otherwise prints the set. It printed the set.

**What is at the top of Task 3 is a different sentence** — "Removing the
asymmetry makes notification strictly LESS reachable, not more" — under a
heading reading "The headline, first, in its own line". That heading is at
**line 773 of an 851-line file**. It is first *within Task 3*, not first
in the document. If document-first placement was the intent, the heading's
wording claims more than was done. Recorded rather than silently fixed:
moving a Task 3 conclusion above Part 1 would make the file open on a
result before its own context.

### Reconciling 0.7 against 0.55 — two gates, not two values for one

They do not conflict. `is_notify_eligible()`
(`app/services/job_scoring.py:180-183`) is a conjunction of **three**
gates, all `>=`:

| gate | value | where |
|---|---|---|
| `final_score >= notification_threshold` | **0.7** | per-user, `user_preferences.notification_threshold`, DB default at `app/db/models/user.py:160-164` |
| `semantic_raw >= semantic_notify_floor` | **0.62** | `app/core/config.py:280` |
| `weight_covered >= min_weight_covered_to_notify` | **0.55** | `app/core/config.py:294` |

The prompt named the coverage floor. Task 3's headline named the score
threshold. Each is correct about its own gate — and the shift between them
**is the finding**: under candidate B, coverage is 1.0 by construction, so
the coverage gate can never bind and the entire question moves onto the
0.7 gate, which is why B yields 0 at every floor.

That said, the headline should have named which gate it meant. A sentence
that changes threshold mid-argument without saying so is the documentary
version of CLAUDE.md §2's "two explanations for one observation means the
checking is not finished."

### Was the weight_covered distribution with counts produced? Yes

`scripts/asymmetry_isolate.py` lines 181-184 print a `Counter` over the
stored `weight_covered` values, one line per observed value. The output is
recorded in Task 3 above:

```
0.35 : 46
0.50 : 242
0.80 : 6
```

Four separate numbers — 46, 242, 6, and 294 pairs — reported rather than
reconciled here, per §2's rule about agent arithmetic.

---

## Proposal for Day 11 — the three missing `scoring_runs` columns

Stop condition 3 kept Part 3 out of this correctly. Written as a
proposal; **no migration generated.**

### The migration

`alembic revision -m "add skip breakdown to scoring_runs"`, with
`down_revision = 'b3f7c21d9e40'` — three `op.add_column` calls on
`scoring_runs` for `users_skipped_no_profile`,
`users_skipped_no_active_cv`, `users_skipped_cv_not_embedded`, and three
`op.drop_column` in `downgrade()`.

### The one real decision: nullable

- `nullable=False, server_default="0"` matches every other counter on
  `scoring_runs` and preserves the table's style. **It is wrong.** It
  makes runs 1-4, which predate the breakdown, assert a measured 0/0/0 —
  and for run 3, the crashed one, that is a positive claim about a run
  that never finished. This is CLAUDE.md §1's "NULL *is* abstain" rule
  reappearing in a new table.
- `nullable=True` is correct. Absent, not zero — the rule `agent_runs`
  already follows for `jobs_enriched` and `ingestion_status`. Old rows
  stay NULL and honestly say "this run predates the breakdown".

### What it would break

1. **The migration alone changes nothing, and will look like it worked.**
   `ScoringRunRepository.finish()` compiles `values(**counters)`, so
   adding the columns merely makes the three keys *legal* again — nothing
   re-adds them to the persisted dict. The second half of the change is
   deleting the comment block at `app/services/job_scoring.py:751-760`
   and restoring the three keys. A migration merged without that is a
   schema change with no writer: three permanently-NULL columns, green
   tests, and a status that says fine.
2. **A second identity the table does not assert.** `scoring_runs` has one
   funnel assertion — `users_considered == users_skipped_no_cv +
   users_scored` (`job_scoring.py:667`). The breakdown adds
   `users_skipped_no_cv == no_profile + no_active_cv + cv_not_embedded`,
   which *is* asserted in the service (`job_scoring.py:703`) but is not a
   property of the table. Rows written before the migration satisfy the
   first and are NULL for the second. The funnel will balance while the
   breakdown drifts.
3. **A second copy of numbers `agent_runs` already holds**, in a row
   `agent_runs.scoring_run_id` already points at. Two copies can disagree
   only if a future change writes them from different places — which is
   exactly the change nobody notices. Whoever adds the columns should
   record in the model docstring which table is authoritative.

### The recommendation: build the test first, and decide the columns separately

`test_every_summary_key_has_a_column` guards **`agent_runs`**. Nothing
guards `scoring_runs` against the dict-versus-schema drift that produced
the `CompileError` on the first real run.

**The valuable half of this proposal is not the migration.** A
`test_every_persisted_scoring_key_has_a_column` — asserting that every key
`run_scoring` puts in the persisted dict has a column on `ScoringRun` —
would have caught the original crash with **no database**, which is the
property that actually failed. It protects the arrangement as it stands
today, whether or not the columns are ever added.

Build that test in Day 11. Leave the columns undecided until someone
decides which table owns the breakdown.

---

# Day 10 Part 4 — the drift test, two reconciliations, and what is carried forward

## The prompt files were not restored, and why

`prompts/day10_part3.md` and `prompts/day10_part3_amendment.md` were **not**
created and there is no commit 0. I was asked to write them myself and
commit them as the missing originals with a note that they "arrived after
the work". I declined that specific framing: Q1 of the handoff review
establishes against `git log --all` that those files never reached this
repository, and a commit presenting files I authored today as the
recovered prompts would make the durability finding read as closed by
making the record untrue — and the finding above would then cite, as the
plan's original assertion, a sentence I had written myself.

Offered instead, both still open: commit the genuine originals verbatim if
they exist elsewhere, or commit clearly-labelled reconstructions under a
name that says so (`day10_part3_reconstructed.md`) with a header stating an
agent wrote them after the fact from the work product.

**The durability finding therefore stays open.** It is now the second time
prompt files have been lost — `day8_open_issues.md` was the first, and
CLAUDE.md §1 already records that the do-not-fix list is a reconstruction
because of it.

## Task 1 — `test_every_persisted_scoring_key_has_a_column`, built

`tests/test_scoring_run_persistence.py`. Four tests. No database, which is
the whole point: the CompileError reached a commit **because** the suite
has no database and every live check before it had been `--dry-run`.

### The seam, and why production code had to move first

The dict `run_scoring` persists was a literal inline inside an
`async with session_scope()` block. Unreachable without a session, so
nothing could assert it against the schema — that is *why* no test caught
the crash, not merely a reason none existed.

Extracted to `build_scoring_run_counters(...)`, a pure module-level
function taking the counters and the seven computed aggregates and
returning the dict. `run_scoring` now calls it. This is the same move, and
the same argument, as `build_run_summary(state)` in `app/workflows/state.py`
— Day 9 decision 3 established the pattern for exactly this reason: a dict
that a test can build is a dict a test can check.

**Proved behaviour-preserving before running anything.** The key list was
parsed out of `git show HEAD:app/services/job_scoring.py` with `ast` and
compared against the new function's output:

```
old inline dict keys : 27
new function keys    : 27
identical as a list  : True
only in old          : []
only in new          : []
```

Identical as an ordered list, not merely as a set.

### Proved it can fail, the same way §1.4's test was proved

§1.4 added a throwaway field to the summary and observed the assertion
fire. Here the stronger version was available: **reintroduce the actual
key that caused the actual crash.** Adding
`"users_skipped_no_profile": counters.users_skipped_no_profile` back to
the persisted dict produced

```
AssertionError: persisted counter keys with no scoring_runs column:
['users_skipped_no_profile'] -- these raise CompileError: Unconsumed
column names on the first non-dry run
```

Two tests failed, which is correct — the drift test and
`test_the_skip_breakdown_is_absent_from_the_persisted_dict`, which exists
to be the reminder that a migration alone changes nothing.

Removed. `app/services/job_scoring.py` verified **byte-identical**
afterwards:

```
before: 15d719189366304ccbb2a5ff4f9c772c
after : 15d719189366304ccbb2a5ff4f9c772c
```

### Counts, as two numbers

| Point | Collected |
|---|---|
| Part 3 baseline | 552 |
| After the drift test | **556** |

556 collected, **556 passed**.

### The other direction, asserted differently again

`_columns() - _persisted_keys()` is enumerated as an exact set of eight —
`id`, `created_at`, `updated_at`, `started_at`, `finished_at`, `status`,
`weights_version`, `error_message` — all written by `start()` or by
`finish()`'s own named arguments rather than through the counters dict.
Same `==` rather than `<=` shape as `test_agent_runs.py` and
`test_all_expected_tables_are_registered`, for the same reason: a new
column cannot appear without somebody acknowledging it in writing.

I guessed a ninth, `trigger`, which does not exist. The test failed on my
guess and was corrected to the eight the model actually has. Worth
recording: the assertion caught an invented column on its first run.

**No migration was generated.** The three columns remain undecided.

## Task 2 — two internal contradictions, both recoverable

### `cv_versions` "9 before, 9 after" versus 12 — the record was right and I was wrong

`SELECT count(*) FROM cv_versions` is **12**. But:

```
cv_versions rows belonging to user 2 : 9
```

`concurrent_claim_dryrun.py` requires `--user-id` and was run against user
2, so its before/after count was **scoped to that user** — which is the
correct scope for a script that operates on one user's CVs. User 2 owns
`cvs` 3 (5 versions) and 22, 23, 24 (4 versions): nine.

**"9 before, 9 after" is correct.** The contradiction was mine: I compared
a whole-table count against a per-user count and reported the mismatch as
the record's error. The Q3 paragraph above stands corrected by this one.

### Day 6's "about ten" versus 7 — the prose is wrong and the table is provably right

`ingestion_runs.pages_fetched`, per run: 2, 2, 1, 2. Four rows, ids 1-4,
no gaps.

The table can be trusted here for a specific structural reason, not
because it is a table. `run_ingestion` opens the row **before any network
call and commits immediately** — "Phase 1: open the run, then get out of
the database before any network call"
(`app/services/job_ingestion.py:242-249`), the same
open-early/complete-late shape as `agent_runs` and `scoring_runs`.
Therefore **any ingestion run that made even one Adzuna call left a row**,
finished or not. No row is unfinished and no id is missing, so no
ingestion run is absent from the table.

All 99 jobs trace to run 1 (`inserted = 99`, `2026-08-31 16:47:47`), which
matches `jobs.created_at` min and max being that single timestamp.

So: **7 is right; "about ten" was an estimate written in prose, and its
own hedge says so.** `docs/Day_6_JobIngestion.md:786` should be corrected
to 7 in Day 11, with the caveat kept.

What stays genuinely unknown is unchanged and is not this contradiction:
probe calls are made outside `run_ingestion` and appear in no table, and a
*failed* call is spent without incrementing `pages_fetched`. The
ingestion-run spend is exact; the total spend is not.

## Task 3 — carried forward, not acted on

Three items to §7 as proposals. None executed.

- **Rename `concurrent_claim_dryrun.py` → `concurrent_claim_probe.py`.**
  Argued in Q3 above; §1 is the wrong home and this is agreed. Not done —
  a rename is a code change and belongs with the day that owns it.
- **CV 24's `extraction_status` reads `failed` while its extracted version
  exists and is embedded.** No write proposed. It needs a decision about
  what `profile_view` should show, not a repair.
- **A read-only stale-claim check — described, not built.** Print every
  `cvs` row at `extraction_status = 'extracting'` with the age of its
  claim (`now - updated_at`) beside `DEFAULT_STALE_AFTER`, so each row
  reads as either *reclaimable* or *genuinely held*. CV 19 would print as
  reclaimable at roughly five days against a fifteen-minute window. The
  value is entirely in the second case, which nothing can currently see:
  a claim younger than the window whose process is dead is indistinguishable
  from a live extraction. Home: `scripts/scorable_targets_check.py`, which
  already exists to make invisible states countable. Read-only, no session
  writes, no API calls — and if it is ever given a `_dryrun` name it must
  mean it.

---

# Day 10 Part 5 — the amendment arrived, and it moves two answers

`prompts/day10_part3.md` (485 lines) and
`prompts/day10_part3_amendment.md` (172 lines) are now in the repository,
placed by the human and committed as commit 0. They arrived **after** the
work, not before. Q1 of the handoff review stands as a statement about
Part 3: `git log --all` proves no Day 10 prompt existed here while the
work was done. What follows is what changes now that both can be read.

I also misread the instruction that produced them — "put the two files in
the repo" was an instruction to commit files already on disk, and I read
it as an instruction to author them and declined. Correcting that here
because the declined-then-complied sequence is otherwise unreadable in
the record.

## What the amendment actually says, against what I assumed

I assumed the amendment would mandate APScheduler and that Part 3 had
overridden it. **Both halves of that are wrong.**

Amendment §C: *"Decide the mechanism before writing it, and record the
decision. The choice is between an OS-level scheduler … and an in-process
scheduler … Do not pick by deferring to either the plan or the original
prompt. Argue it, choose, and write the argument into
`docs/Day_10_progress.md` before the code."*

So Windows Task Scheduler was an explicitly permitted outcome. The
amendment does not name a winner; it forbids picking one by deference.

**And it reached my exit-code argument on its own, before I did.**
Amendment §B: *"Under APScheduler the graph is invoked in-process and that
exit-code path never runs — so the mechanism the plan names removes the
property §2.1 is built on."* That is the same argument as Q1's, arrived at
independently. Corroboration, and it means the conclusion was available at
the time.

### This revises Q1's "non-compliance" verdict

Amendment §B, on the Day 9 decision 2 precedent: *"Divergence is allowed
here. What is not allowed is diverging without saying so."*

So the divergence was **pre-authorised**. Q1 called this plan
non-compliance and contrasted it with Day 9 decision 2's plan gap. The
distinction between the two kinds of divergence still holds and I would
write it again. But the verdict was wrong in a way that matters: the
failure was never the choice. **The failure was making an authorised
choice silently** — which is the one thing the amendment names as not
allowed. A defensible decision, taken without the argument being written,
is indistinguishable afterwards from carrying the original instruction
forward unexamined. That is what Q1 had to reconstruct.

The ordering requirement was also missed independently of the amendment:
"before the code". Q1's argument was written five days after the code.

## §C's four required answers — two were missing

§C requires four things in the record whichever mechanism is chosen. Q1
answered the first. Items 3 and 4 are conditional on APScheduler and do
not apply. **Item 2 was never answered, and neither was §C's closing
question.** Both are answered here.

### §C.2 — silent death, how a stopped scheduler becomes visible

The honest answer first: **today, it does not.** Neither mechanism is
observed by anything in this repository, and this is the weakest point in
Day 10's unattended story.

How the two die differently:

| | Windows Task Scheduler | APScheduler |
|---|---|---|
| host process exits | survives — the task is an OS object | **scheduler is gone permanently** |
| machine reboots | survives by design | gone unless something restarts the host |
| dies from | disabled, deleted, expired task credentials | any unhandled exit of one Python process |
| last outcome readable without the repo | yes — `Get-ScheduledTaskInfo` gives `LastRunTime` / `LastTaskResult` | no — only whatever it logged before dying |

Task Scheduler is the more survivable of the two and leaves an
OS-queryable last result. That is a real advantage and it is **not** the
same as being visible.

Because on a night that never ran, `run_nightly.ps1` writes no log, and
nothing looks for a log that is absent. `agent_runs` has the same shape: a
skipped night is a **missing row**, and a missing row is exactly what
`CLAUDE.md` §0 warns about — indistinguishable from a quiet night, and
from a healthy system nobody is asking about.

**What would actually pay for this, named and not built:** a staleness
check over `agent_runs.started_at` — if the newest row is older than about
26 hours, say so loudly. It is one query, it needs no scheduler
cooperation, and it works identically under either mechanism because it
observes the *work* rather than the *plan* — the same distinction Part 1
used to reject the aggregate query in `scorable_targets_check.py`. Nothing
in Day 10 does this. It is the gap, and it should be Day 11's.

### §C's closing question — which of the three the other mechanism handles free

§2.2 named overlapping runs, missed runs and log growth. Which would
APScheduler have handled for free:

- **Overlapping runs — near-free under APScheduler.** `max_instances`
  defaults to 1, so the drop happens with no configuration. Task Scheduler
  needed `-MultipleInstances IgnoreNew` stated explicitly. One flag against
  zero: cheap either way, marginal win to APScheduler.
- **Missed runs — Task Scheduler wins, and not marginally.**
  `misfire_grace_time` and `coalesce` only apply while the host process is
  alive; if the process was down, there is no misfire to grace, because
  there was no scheduler. `-StartWhenAvailable` genuinely covers a machine
  asleep at 03:00. This is the opposite of a free win for the alternative.
- **Log growth — the one clear APScheduler win.** It logs through Python's
  `logging`, where `RotatingFileHandler` is one line of configuration.
  Under Task Scheduler, rotation is a PowerShell problem nobody has solved,
  and Part 3 declined to solve it on the grounds that a rotation scheme can
  delete the evidence of the run you most wanted to read.

Net: one clear win to APScheduler (log rotation), one clear win to Task
Scheduler (missed runs while the machine was off), one marginal. Nothing
here overturns the exit-code argument, which remains the decisive one.

## §A was deliberately not executed

Amendment §A asks that `prompts/day10_part3.md` be edited in place —
§2.2 and §2.4 replaced by §C and §D, with an "Amendment 1" note at the
top — and committed on its own before any code.

**Not done, and the reason is that the precondition is five days gone.**
That fold exists so an agent about to work does not read a superseded
instruction. The work is finished. Folding now would overwrite the §2.2
that the delivered code was actually written against, leaving a prompt
file that no longer shows what was asked — and §A's own justification is
that "a corrected copy that pretends otherwise is worse than the wrong
one". Applying it late inverts it.

So both files are committed **verbatim and unfolded**, with §2.2 and §C
sitting in the repository disagreeing with each other, which is the true
state of the record. Whether to fold them now is a decision for the human,
not a tidy-up for an agent to perform on its own initiative.
