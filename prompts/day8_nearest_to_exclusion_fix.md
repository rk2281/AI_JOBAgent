# PROMPT — Day 8: fix the `nearest_to` / `is_excluded` limit mismatch

Save this to `prompts/day8_nearest_to_exclusion_fix.md`. Do not paste it
into PowerShell.

---

## HARD RULES — read before anything else

1. **Never read, print, echo, cat, or open `.env`.** Not to check a
   value, not to confirm a variable exists, not inside an error
   message. If a command would print a connection string, do not run
   it. `scripts/query.py` already loads config on its own; you never
   need to look at the file.
2. **Stop at the end of every STAGE and report.** Do not begin the next
   stage until told to continue. A stage that "seemed fine" still stops.
3. **Never confirm a total.** When asked for numbers, print each number
   separately, exactly as the command emitted it. Do not add them, do
   not multiply them, do not state whether they agree, do not write
   "as expected" or "matches". Report digits and stop.
4. **Never invent a number.** If a command failed or printed nothing,
   say "no output" and stop. Do not supply a plausible value.
5. **Do not use `python -c`.** This shell sometimes prints nothing for
   `python -c`, with no error. Write a file and run it with
   `python -m scripts.NAME`, or use `python -m scripts.query "SQL"`.
6. **Windows / PowerShell.** All scripts run as `python -m scripts.name`
   from the repository root.
7. **`pytest-asyncio` is NOT installed and must NOT be installed.** Do
   not add it to `requirements.txt`. Async cases are driven by
   `asyncio.run()` inside ordinary sync test functions.
8. **Respect layering.** No SQL in handlers. No Telegram imports in
   services. No business logic in repositories. `app/integrations/` is
   the only place a third-party SDK is imported.
9. **Do not create files, migrations, or tests that this prompt does
   not ask for.** In particular: **no new Alembic migration.** The head
   stays `9a4e7c1d5b82` and the count stays 8.
10. **Make only the edits written below.** Every edit is given as an
    exact find string and an exact replace string. If a find string
    does not match the file byte-for-byte, **stop and report the
    mismatch**. Do not adapt, reformat, or guess.

---

## BACKGROUND — the bug being fixed

`JobRepository.nearest_to()` filters `is_active = TRUE` and
`embedding IS NOT NULL`. It does **not** filter `is_excluded`.

`run_scoring()` calls it with `limit = counters.jobs_scored`, and
`jobs_scored` is `len(list_scorable_jobs())`, which **does** filter
`is_excluded`.

So the limit is smaller than the pool it is drawn from. Every excluded
job that lands inside the returned window occupies a slot, gets skipped
by the service, and pushes one real job off the far end of the window.
That job is never scored, never ranked, never counted, and nothing
anywhere reports it.

The existing funnel assertion does not catch this, because every number
in it is computed from repository counts before the loop runs. It
checks the plan, not the execution.

The fix has three parts, and all three are required:

- **the limit** — ask `nearest_to` for the whole pool it actually
  searches (active AND embedded, *ignoring* exclusion), then drop
  excluded ids in the service;
- **`ef_search`** — it currently scales to `jobs_scored`. Once the
  limit is the larger pool number, `ef_search` must scale to that
  larger number too, or HNSW returns fewer rows than the limit asked
  for, silently;
- **a counted drop and a new assertion** — so that if this ever breaks
  again, a number changes.

---

## STAGE 0 — report the current state. No edits.

Run each command and paste its output verbatim.

```powershell
git rev-parse --short HEAD
```

```powershell
python -m alembic current
```

```powershell
python -m pytest -q
```

Report the passing test count as a bare number.

Now the four job counts. Run this as **one** command:

```powershell
python -m scripts.query "SELECT (SELECT count(*) FROM jobs WHERE is_active) AS active, (SELECT count(*) FROM jobs WHERE is_active AND embedding IS NOT NULL) AS active_embedded, (SELECT count(*) FROM jobs WHERE is_active AND embedding IS NOT NULL AND NOT is_excluded) AS scorable, (SELECT count(*) FROM jobs WHERE is_active AND is_excluded) AS excluded"
```

Report the four numbers as four separate labelled lines. Do not compare
them to each other.

Then:

```powershell
python -m scripts.query "SELECT user_id FROM profiles ORDER BY user_id"
```

**STOP. Report and wait.**

---

## STAGE 1 — construct the failing case, and measure it

Read the `excluded` number from Stage 0.

**If `excluded` is 0**, the bug is currently latent — before/after
numbers would be identical and would prove nothing. Job 2 is the known
junk posting (its description names no skills; investigated and
confirmed). It is meant to be excluded permanently. Run:

```powershell
python -m scripts.query "UPDATE jobs SET is_excluded = true, exclusion_reason = 'recruiter self-introduction, description names no skills' WHERE id = 2 AND NOT is_excluded"
```

Then re-run the four-count query from Stage 0 and report the four
numbers again, separately.

**If `excluded` is already 1 or more**, run nothing here and say so.

Now measure the current (broken) behaviour. Substitute the user id you
were given for `<USER_ID>`:

```powershell
python -m scripts.score_jobs --user-id <USER_ID> --dry-run
```

`--dry-run` writes no `scoring_runs` row and no `recommendations` rows,
so this is safe to repeat.

Report **only these five lines**, copied exactly as printed:

```
jobs_considered           ?
jobs_excluded_manual      ?
jobs_scored               ?
users_scored              ?
pairs_scored              ?
```

Do not multiply `jobs_scored` by `users_scored`. Do not say whether
`pairs_scored` looks right.

**STOP. Report and wait.**

---

## STAGE 2 — the repository count

**File:** `app/db/repositories/job.py`

One new method. It goes immediately after
`count_active_missing_embedding_scorable`, at the end of
`JobRepository`.

**FIND** (exact — this block ends the class):

```python
                Job.is_active.is_(True),
                Job.embedding.is_(None),
                Job.is_excluded.is_(False),
            )
        )
        return int(result.scalar_one())


class IngestionRunRepository:
```

**REPLACE WITH:**

```python
                Job.is_active.is_(True),
                Job.embedding.is_(None),
                Job.is_excluded.is_(False),
            )
        )
        return int(result.scalar_one())

    async def count_active_embedded_jobs(self) -> int:
        """The candidate pool nearest_to() actually searches.

        Active and embedded, INCLUDING excluded rows -- deliberately
        the one Day 8 count that ignores `is_excluded`, because it
        exists to match nearest_to()'s own WHERE clause exactly. Any
        divergence between the two is the bug this method was added
        to close: a limit drawn from a smaller set than the query
        selects from silently truncates the far end of the result.

        Counted independently rather than derived as
        count_active_jobs() - count_active_missing_embedding(). Those
        two subtract to the same value today, and a subtraction can
        never disagree with itself -- it would make the funnel
        assertion an equation solved to be true rather than a check.
        Two queries that can disagree are the point.
        """
        result = await self._session.execute(
            select(func.count())
            .select_from(Job)
            .where(
                Job.is_active.is_(True),
                Job.embedding.isnot(None),
            )
        )
        return int(result.scalar_one())


class IngestionRunRepository:
```

Verify the file still imports and the method exists:

```powershell
python -m pytest -q
```

Report the passing test count as a bare number.

```powershell
python -m scripts.query "SELECT count(*) AS pool FROM jobs WHERE is_active AND embedding IS NOT NULL"
```

Report that number.

**STOP. Report and wait.**

---

## STAGE 3 — the service

**File:** `app/services/job_scoring.py`

Four edits. Apply all four, then run nothing until the end of the
stage.

### 3a — two new counters

**FIND:**

```python
    pairs_scored: int = 0
```

**REPLACE WITH:**

```python
    pairs_scored: int = 0

    # Two separate numbers for two separate causes, because they are
    # not the same event and folding them into one silent `continue`
    # is what let the limit bug hide. nearest_dropped_excluded is
    # expected and should equal the number of excluded rows that are
    # active and embedded. nearest_dropped_unscorable is expected to
    # be ZERO: it can only be non-zero if list_scorable_jobs() and
    # nearest_to(), read in two different sessions, disagree about
    # which jobs exist -- a row exclusion or an embedding changing
    # underneath the run. If it is ever non-zero, that is a finding
    # about concurrency, not a rounding detail.
    nearest_dropped_excluded: int = 0
    nearest_dropped_unscorable: int = 0
```

### 3b — count the pool

**FIND:**

```python
        scorable_jobs = await job_repository.list_scorable_jobs()
```

**REPLACE WITH:**

```python
        # The pool nearest_to() searches, which is NOT the same set as
        # scorable_jobs: it includes excluded rows. Read here, in the
        # same session as the counts above, so all four numbers
        # describe one moment.
        nearest_pool_size = await job_repository.count_active_embedded_jobs()
        scorable_jobs = await job_repository.list_scorable_jobs()
```

### 3c — the limit and `ef_search`

**FIND:**

```python
        ef_search = max(counters.jobs_scored, _MIN_EF_SEARCH)
```

**REPLACE WITH:**

```python
        # Scales to the POOL, not to jobs_scored. pgvector's HNSW keeps
        # only ef_search candidates in flight, so an ef_search below the
        # LIMIT returns fewer rows than were asked for -- with no error.
        # At 99 rows the planner picks a sequential scan and this never
        # bites; it would begin biting silently once the table outgrows
        # that, which is the worst possible time to find out.
        ef_search = max(nearest_pool_size, _MIN_EF_SEARCH)
```

**FIND:**

```python
                nearest = await JobRepository(session).nearest_to(
                    vector, limit=counters.jobs_scored, ef_search=ef_search
                )
```

**REPLACE WITH:**

```python
                # limit is the pool size, not jobs_scored. nearest_to()
                # does not filter is_excluded, so asking it for
                # jobs_scored rows draws a smaller window than the set
                # it selects from: every excluded job inside that
                # window takes a slot and pushes one real job off the
                # far end, unranked and uncounted. Ask for everything
                # the query can return, then drop the excluded ids
                # below where the drop can be counted.
                nearest = await JobRepository(session).nearest_to(
                    vector, limit=nearest_pool_size, ef_search=ef_search
                )
```

### 3d — split the skip, and count both halves

**FIND:**

```python
                for job, distance in nearest:
                    # nearest_to scopes to active+embedded only, not to
                    # is_excluded -- see JobRepository.nearest_to. An
                    # excluded job surfacing here is skipped rather
                    # than scored; it was already counted once, at the
                    # run level, in jobs_excluded_manual above.
                    if job.is_excluded or job.id not in jobs_by_id:
                        continue
```

**REPLACE WITH:**

```python
                for job, distance in nearest:
                    # nearest_to scopes to active+embedded only, not to
                    # is_excluded -- see JobRepository.nearest_to. An
                    # excluded job surfacing here is skipped rather
                    # than scored; it was already counted once, at the
                    # run level, in jobs_excluded_manual above.
                    #
                    # Two conditions, two branches, two counters. They
                    # were one `if` with an `or`, and that is precisely
                    # why the limit bug was invisible: both causes
                    # produced the same nothing. The second branch
                    # should never fire.
                    if job.is_excluded:
                        counters.nearest_dropped_excluded += 1
                        continue
                    if job.id not in jobs_by_id:
                        counters.nearest_dropped_unscorable += 1
                        continue
```

### 3e — the third funnel assertion

**FIND:**

```python
    assert counters.jobs_considered == (
        counters.jobs_skipped_no_embedding + counters.jobs_excluded_manual + counters.jobs_scored
    ), (
        f"scoring funnel does not balance: considered={counters.jobs_considered} "
        f"skipped_no_embedding={counters.jobs_skipped_no_embedding} "
        f"excluded_manual={counters.jobs_excluded_manual} scored={counters.jobs_scored}"
    )
```

**REPLACE WITH:**

```python
    assert counters.jobs_considered == (
        counters.jobs_skipped_no_embedding + counters.jobs_excluded_manual + counters.jobs_scored
    ), (
        f"scoring funnel does not balance: considered={counters.jobs_considered} "
        f"skipped_no_embedding={counters.jobs_skipped_no_embedding} "
        f"excluded_manual={counters.jobs_excluded_manual} scored={counters.jobs_scored}"
    )
    # The two assertions above are computed entirely from repository
    # counts taken BEFORE the scoring loop. They describe what the run
    # intended to do. Both of them balanced perfectly while the limit
    # bug was dropping real jobs, because neither one looks at what
    # nearest_to() actually returned.
    #
    # This third one does. Every scored user must produce exactly one
    # pair per scorable job -- no more, and critically no fewer. It is
    # the only check here that can fail because of something the loop
    # did rather than something the plan said.
    assert counters.pairs_scored == counters.jobs_scored * counters.users_scored, (
        f"scoring funnel does not balance: pairs_scored={counters.pairs_scored} "
        f"jobs_scored={counters.jobs_scored} users_scored={counters.users_scored} "
        f"dropped_excluded={counters.nearest_dropped_excluded} "
        f"dropped_unscorable={counters.nearest_dropped_unscorable}"
    )
```

### 3f — the two counters into the returned dict ONLY

Add them to the `return` dict. **Do NOT add them to the `counters={...}`
dict passed to `ScoringRunRepository.finish()`.** That dict is splatted
straight into an ORM `update().values()`, so a key with no matching
column raises at run time — persisting these two would require a ninth
migration, and this prompt does not add one.

**FIND:**

```python
        "pairs_scored": counters.pairs_scored,
        "abstain_semantic": counters.abstain_semantic,
        "abstain_skill": counters.abstain_skill,
        "abstain_experience": counters.abstain_experience,
        "abstain_location": counters.abstain_location,
        "abstain_title": counters.abstain_title,
        "semantic_clamped_low": counters.semantic_clamped_low,
        "semantic_clamped_high": counters.semantic_clamped_high,
        "semantic_raw_min": semantic_raw_min,
```

**REPLACE WITH:**

```python
        "pairs_scored": counters.pairs_scored,
        "nearest_dropped_excluded": counters.nearest_dropped_excluded,
        "nearest_dropped_unscorable": counters.nearest_dropped_unscorable,
        "abstain_semantic": counters.abstain_semantic,
        "abstain_skill": counters.abstain_skill,
        "abstain_experience": counters.abstain_experience,
        "abstain_location": counters.abstain_location,
        "abstain_title": counters.abstain_title,
        "semantic_clamped_low": counters.semantic_clamped_low,
        "semantic_clamped_high": counters.semantic_clamped_high,
        "semantic_raw_min": semantic_raw_min,
```

**Note:** this find string appears twice in the file if you match
loosely — once in the `finish()` call (where keys are quoted the same
way) and once in the `return`. The `finish()` version has
`"pairs_scored": counters.pairs_scored,` indented **20 spaces**; the
`return` version is indented **8 spaces**. The block above is the
8-space one. If your tool matches the wrong one, **stop and report it**
rather than editing by hand.

Now run:

```powershell
python -m pytest -q
```

Report the passing test count as a bare number.

**STOP. Report and wait.**

---

## STAGE 4 — the script output

**File:** `scripts/score_jobs.py`

**FIND:**

```python
        print(f"pairs_scored              {result['pairs_scored']}")
        print("--- abstains")
```

**REPLACE WITH:**

```python
        print(f"pairs_scored              {result['pairs_scored']}")
        print("--- nearest_to drops")
        print(f"nearest_dropped_excluded  {result['nearest_dropped_excluded']}")
        print(f"nearest_dropped_unscorable {result['nearest_dropped_unscorable']}")
        print("--- abstains")
```

**STOP. Report the edit applied and wait.**

---

## STAGE 5 — measure again, same command as Stage 1

```powershell
python -m scripts.score_jobs --user-id <USER_ID> --dry-run
```

Report **only these seven lines**, copied exactly as printed:

```
jobs_considered           ?
jobs_excluded_manual      ?
jobs_scored               ?
users_scored              ?
pairs_scored              ?
nearest_dropped_excluded  ?
nearest_dropped_unscorable ?
```

Then:

```powershell
python -m pytest -q
```

Report the passing test count as a bare number.

```powershell
git rev-parse --short HEAD
python -m alembic current
```

Do **not** compare Stage 5's numbers to Stage 1's. Do not say the fix
worked. Print the numbers and stop.

**STOP. Report and wait.**

---

## EXPECTED VALUES

Fill this in from the reported output. The right-hand columns are what
each number should be **if** Stage 0 reported 99 active, 99 active and
embedded, and exactly one excluded job, with one user scored.

| number | Stage 1 (before fix) | Stage 5 (after fix) |
|---|---|---|
| `jobs_considered` | 99 | 99 |
| `jobs_skipped_no_embedding` | 0 | 0 |
| `jobs_excluded_manual` | 1 | 1 |
| `jobs_scored` | 98 | 98 |
| `users_scored` | 1 | 1 |
| `pairs_scored` | **97** | **98** |
| `nearest_dropped_excluded` | — (not printed) | **1** |
| `nearest_dropped_unscorable` | — (not printed) | **0** |
| pytest passing | 339 | 339 |
| alembic head | `9a4e7c1d5b82` | `9a4e7c1d5b82` |

### How to read a result that does not match

- **Stage 1 `pairs_scored` came out 98, not 97.** The bug did not bite
  on this run. With a limit of 98 drawn from a pool of 99, exactly one
  job is cut — the single farthest one. If the excluded job happened to
  be that farthest job, nothing was lost this time. This is **not**
  evidence the bug is not real; it is one draw where the excluded row
  sat at the far end. Stage 5 must still report 98, and
  `nearest_dropped_excluded` must still be 1.
- **Stage 5 `pairs_scored` is still below `jobs_scored` × `users_scored`.**
  The new assertion should have fired and crashed the run before
  printing. If it printed instead, the assertion edit did not land.
- **`nearest_dropped_unscorable` is non-zero.** `list_scorable_jobs()`
  and `nearest_to()` disagreed about which jobs exist. Report the
  number; do not fix it.
- **pytest count fell below 339.** Stop. Report which tests failed by
  name. Do not repair a test to make it pass.
- **A find string did not match.** Stop and paste the surrounding ten
  lines of the real file.

---

## WHAT THIS WOULD LOOK LIKE IF IT SILENTLY DID NOTHING

If every edit above were applied to a file that was never imported, or
the limit change were reverted by a bad merge, the run would still
finish, still report `status complete`, still write recommendations,
and both original funnel assertions would still balance. The **only**
numbers that would move are `pairs_scored` and the two new drop
counters. That is the entire reason they exist. If Stage 5 reports a
clean run with `nearest_dropped_excluded` at 0 while Stage 0 reported a
non-zero excluded count, the fix did not take effect — an excluded job
in the pool must be seen and dropped, and a zero there means the pool
was never widened.

---

## DO NOT

- Do not add `is_excluded` filtering inside `nearest_to()`.
  `app/services/job_search.py` calls it twice for Day 7 search, and
  changing its scope would silently change search results for a
  consumer that never asked for it.
- Do not create a migration.
- Do not add `nearest_dropped_*` to the `finish()` counters dict.
- Do not write new tests in this prompt. Tests for Parts 5 and 6 are a
  separate task.
- Do not touch `app/services/scoring.py` or
  `app/services/scoring_signals.py`.
- Do not run `score_jobs` without `--dry-run`.
- Do not commit.
