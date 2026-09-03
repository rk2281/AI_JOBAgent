# Day 9 — what was built, and how it was verified

The decisions and their reasoning are in `docs/Day_9_Design_Note.md`.
This file records what happened, in the order it happened, with the
numbers.

Test count: **363 at the start, 471 at the end.** Reported as two
numbers rather than a delta, per `CLAUDE.md` §2.

Alembic head unchanged: `9a4e7c1d5b82`, 8 migrations.

---

## Part 1. A process failure found before any code was written

`git status --short --untracked-files=all` at the start of Day 9 showed
that **HEAD contained the Day 8 migration but none of the Day 8 code.**

`alembic/versions/9a4e7c1d5b82_add_day8_scoring_tables.py` was
committed. `app/services/job_scoring.py`, `app/db/repositories/scoring.py`
and `tests/test_job_scoring.py` were not. HEAD had 19 test files; the
working tree had 20.

`scripts/pack.ps1` builds from `git archive`, which emits only the
committed tree. So **every archive produced was a repository with the
scoring tables and no scoring service** — it would not import, and the
363-test baseline could not be reproduced from it. `pack.ps1` itself was
also untracked: the archive script was missing from every archive it
built.

`CLAUDE.md` §1 already recorded that the original do-not-fix list "lived
in `prompts/day8_open_issues.md`, which was absent from the archive it
was reconstructed from". That was not a one-off. It is this mechanism,
and it was still armed.

Recorded in `CLAUDE.md` §3:

> **A valid archive can still be a useless one.** Untracked project
> instructions and records are invisible to `git archive`; therefore a
> self-testing pack script can produce a structurally valid archive that
> silently loses the context required to operate the project. `pack.ps1`
> asserts what it excluded, not what it should have included — it cannot
> assert what was never committed.

### 1.1 The context files

`.claude/commands/claude.md` was **moved** (not copied) to repo-root
`CLAUDE.md`. Files under `.claude/commands/` are slash commands; Claude
Code auto-loads `CLAUDE.md` from the root. So the file's own claim that
it "loads automatically at the start of every session" was false as
placed — it loaded only if somebody typed `/claude` — and `guard.md`'s
two references to "section 1 of `CLAUDE.md`" resolved to nothing.

No `AGENTS.md` was created. The instruction to make one is conditional
on using a tool that reads it; nobody is. Creating a copy would
manufacture the drift the same paragraph warns against.

Five paths tracked, enumerated individually rather than by directory:
`CLAUDE.md`, `.claude/commands/guard.md`, `docs/Day_8_progress.md`,
`prompts/day8_nearest_to_exclusion_fix.md`,
`prompts/day8_part56_tests.md`.

`prompts/` was **not** staged as a directory. If
`prompts/day8_open_issues.md` ever resurfaces it must be a deliberate
addition, not something a trailing slash swept in.

---

## Part 2. Stage 1 — dependency reconnaissance

| | Before | After |
|---|---|---|
| packages in `pip freeze` | 57 | 72 |
| `pip check` | no broken requirements | no broken requirements |
| tests | 363 passed | 363 passed |

`langgraph==1.2.11` added 15 packages with **zero removals, zero
upgrades, zero downgrades**. The `pip freeze` diff contained only
additions.

Every at-risk pin verified unchanged: `pydantic` 2.13.4,
`pydantic_core` 2.46.4, `typing_extensions` 4.16.0, `tenacity` 9.1.4,
`httpx` 0.28.1, `websockets` 16.1.1. `langchain-core` 1.6.1 requires
`pydantic>=2.7.4`, already satisfied. **The `google-genai` /
`websockets<17.0` pin documented at the bottom of `requirements.txt` was
not touched.**

`requirements.txt` was verified identical to the installed environment
after the edit — 72 pins, no drift.

Three pins that had never been recorded (`httpcore2`, `httpx2`,
`truststore`) were already installed before Day 9. They were committed
as their own documented changeset. **They have not been verified by this
work** — see Part 7.

---

## Part 3. Stage 3 — one definition of "scorable user"

Guard checklist first: nothing on the do-not-fix list touches target
resolution, one call site, no logging of anything originating in
`app/integrations/`. `AdzunaClient` already redacts provider errors
through `describe_http_error()` and raises `from None` specifically
because the URL carries `app_id` and `app_key`, so the nodes read the
already-safe `result.error` and never format an exception of their own.

Three additions to `app/services/job_scoring.py`:

- `is_scorable_user()` — the rule, pure, four boundary cases testable
  without a database
- `select_target_user_ids()` — was `_target_user_ids`, now public
- `resolve_scoring_targets()` — owns its session, returns ints only

**Behaviour unchanged**, proven two ways:

| | Before extraction | After extraction |
|---|---|---|
| `users_considered` | 3 | 3 |
| `users_skipped_no_cv` | 0 | 0 |
| `users_scored` | 3 | 3 |
| `jobs_scored` | 98 | 98 |
| `pairs_scored` | 294 | 294 |

And `scripts/scorable_targets_check.py`, whose oracle shares no code
with what it checks:

```
oracle scorable user ids        : [2, 3, 10]
resolver target_user_ids        : [2, 3, 10]
AGREE.
users_scored 3 vs oracle 3      — AGREE
```

Four states printed separately, today `0 / 0 / 3`. States 2 and 3 are
one `users_skipped_no_cv` to scoring, and only state 3 is fixable by
running the embedding pass — see the open issue in Part 7.

The independence guard was itself verified: adding the forbidden import
made `test_scorable_targets_check_does_not_reference_the_shared_predicate`
fail with the expected assertion, and restoring left the script
byte-identical. **A test that cannot fail is not a test.**

---

## Part 4. Stages 4–8 — the workflow

`app/workflows/`: `state.py`, `routing.py`, `nodes.py`, `graph.py`, plus
`scripts/run_agent.py`.

Test files added, with counts at the point each was written:

| File | Tests |
|---|---|
| `tests/test_workflow_state.py` | 31 |
| `tests/test_workflow_routing.py` | 29 |
| `tests/test_workflow_nodes.py` | 26 |
| `tests/test_workflow_graph.py` | 16 |

Three tests failed on first run and each was a real finding, not a
flake:

1. Two graph-execution tests reached a live database because
   `enrich_jobs` was the one stage a dry run still **calls**, and it had
   not been stubbed. Fixed by stubbing it.
2. `test_langgraph_is_imported_in_exactly_one_module` checked text
   rather than imports, and `__init__.py` mentions langgraph in prose.
   Rewritten to parse imports. The test was wrong; the code was right.

### 4.1 LangGraph introspection, verified rather than assumed

`compiled.get_graph()` exposes `.nodes` (including `__start__` /
`__end__`) and `.edges` (`Edge` objects with `source`, `target`,
`conditional`, `data`). The path test walks those.

One thing this revealed: **both notification branches deduplicate into a
single drawn edge**, because both currently target `finalise`. So
reachability cannot be proven by reading edges and is proven by
executing the graph instead. Had this been assumed rather than checked,
the reachability test would have been vacuous while passing.

---

## Part 5. Stage 9 — the first real run

**Prediction recorded before the run:** status `dry_run`; skips
`discover_jobs: dry_run` and `embed_jobs: dry_run`; attempts resolve /
enrich / score / decide / finalise; computes `enrich_jobs` and
`score_and_rank`; persists nothing; 3 scorable users; 3 × 98 = 294
pairs; `notify_branch = no_qualifying`; `notify_eligible = 0`.

`python -m scripts.run_agent --dry-run`, verbatim:

```
status                    dry_run
dry_run                   True
--- what happened
computation_performed     True
persistence_performed     False
writes_prevented          True
--- stages_attempted
  resolve_targets
  enrich_jobs
  score_and_rank
  decide_notification
  finalise
--- stages_skipped
  discover_jobs: dry_run
  embed_jobs: dry_run
--- stages_computed
  enrich_jobs
  score_and_rank
--- stages_persisted
  (none)
--- errors
  (none)
--- targets
users_considered          3
users_with_profile        3
users_with_embedded_cv    3
--- stages
enrichment_status         dry_run
scoring_status            dry_run
users_scored              3
jobs_scored               98
pairs_scored              294
jobs_skipped_no_embedding 0
--- notification
notify_branch             no_qualifying
notify_eligible           0
terminal_reason           None
```

**Prediction correct in every particular.**

Verified nothing was written: `recommendations` still holds 98 rows with
`scoring_run_id [2]`, unchanged from before the run.

Note `jobs_enriched` and `enrichment_remaining_null` print as `None`,
not `0`. The dry-run enrichment path returns before computing them, so
they are **absent, not zero** — the same distinction the signal columns
on `recommendations` make. Defaulting them to 0 would be the same
mistake as defaulting an abstained signal.

---

## Part 6. What Day 10 can assume

- **The graph runs end to end** against real data and writes nothing
  under `--dry-run`.
- **`build_run_summary(state) -> dict` is pure** — no clock, no
  database — and returns exactly the fields an `agent_runs` row would
  hold. Day 10's migration persists a dict that already exists.
- **`scripts/run_agent.py` exits non-zero** on `failed` or `degraded`,
  so a scheduler can tell a problem from a quiet night without parsing
  output.
- **Quota exhaustion is `degraded`, never a quiet success.**
- **A run that computed nothing reports `complete_no_work`**, which
  outranks `dry_run` in the status precedence.
- **Every skipped stage carries a reason**, so eleven consecutive
  skipped enrichments read as eleven skips rather than as counters that
  never moved.

### What Day 10 must NOT assume

- **That `notify_eligible > 0` is achievable.** It is not, today. The
  branch is proven reachable by test, not by data. See Design Note §9.
- **That `--dry-run` rehearses the pipeline.** It rehearses scoring
  only; ingestion and embedding are skipped outright.
- **That `jobs_enriched` being `None` means zero.**
- **That the coverage floor is a dial.** It is a step function with
  three observed values; 0.45 and 0.40 change nothing today.

---

## Part 7. Open issues carried into Day 10

1. **`users_skipped_no_cv` conflates two different states.** "No active
   CV version" and "active version not embedded" are indistinguishable
   to every caller, and only the second is fixable by running a stage.
   Pre-existing, deliberately not fixed in Day 9 — fixing it in the
   graph would make the graph's definition of scorable differ from
   scoring's. **Needs a decision.**
2. **`httpcore2`, `httpx2` and `truststore`** were recorded in
   `requirements.txt` for the first time on Day 9. They were already
   installed and are not Day 9 additions, but they have not been
   verified. Worth one look before the next archive.
3. **`langsmith` is installed** as a `langchain-core` dependency. It is
   a telemetry client that activates on `LANGCHAIN_TRACING_V2` /
   `LANGSMITH_*` environment variables. Nothing in this repository sets
   or reads them — verified by searching the source, not by reading
   `.env`. Worth confirming none is set in the deployment environment
   before Day 10 runs the graph unattended, since an enabled tracer
   would ship graph state to a third party.
4. **Everything Day 8 left open remains open**: the abstention
   asymmetry, the `||` multi-company field, the possibly-incomplete
   agency list, Adzuna credential rotation, job 81's attempts ceiling.

---

## Part 8. Status of Part 7, after the Day 10 review

The text of Part 7 above is left exactly as written. A record that is
edited to match what turned out to be true stops being a record. What
follows is what a review of the Day 9 archive established.

### Issues 2 and 3 were one issue, and it is closed

Part 7 listed `httpcore2` / `httpx2` / `truststore` as unverified pins
of unknown origin (issue 2), and `langsmith` as a separate telemetry
concern (issue 3). **They are the same issue.** Verified with `pip show`:

```
langgraph -> langchain-core -> langsmith -> httpx2 -> httpcore2
                                                   -> truststore
```

`httpx2` reports `Required-by: langsmith`; `truststore` reports
`Required-by: httpcore2, httpx2`. So the three packages are present
*because* the telemetry client is. They are transitive dependencies, not
loose pins, and they are not removable while `langgraph` is a
dependency.

Two corrections to how this was first described to me. `truststore` is
required by **both** `httpcore2` and `httpx2`, not by `httpcore2` alone.
And `starlette[full]` does declare `httpx2>=2.0.0` as a second path in,
but that extra is **not installed here** — `pip show starlette` requires
only `anyio` and `typing-extensions` — so `langsmith` is today the only
actual path.

Consequence worth carrying: `httpcore2` requires `truststore`, so TLS on
the `httpx2` path verifies against the OS trust store rather than
`certifi`. **No first-party traffic uses that path.** `google-genai`,
`python-telegram-bot` and `langgraph-sdk` all require plain `httpx`
0.28.1, and no module in this repository imports `httpx2`, `httpcore2`
or `truststore`. Recorded as a comment above the pin.

The telemetry half is closed by `assert_tracing_disabled()` in
`app/core/config.py`, called as the first statement of `build_graph()`.
Part 7 said searching the source proves this repo does not enable
tracing; that was true and it was not enough, because `langsmith`
activates from the process environment alone and the deployment host is
not this repository. The check now raises rather than warns — a warning
about telemetry is read after the run that already sent the data — and
it reports variable **names only**, because two of them are credentials.

### Issue 1 is closed

`users_skipped_no_cv` now has a breakdown:
`users_skipped_no_profile`, `users_skipped_no_active_cv`,
`users_skipped_cv_not_embedded`. Only the third is fixable by running
the embedding pass, which is the distinction the single counter could
not express.

`is_scorable_user` is **unchanged**. Its docstring said not to split it
and that instruction was correct: the gate stays a function of exactly
two booleans, and `classify_skip_reason()` is reporting layered on top,
called only after the gate has already returned `False`. It asserts
rather than returning a fallback if handed a scorable user, because a
plausible-looking string there would let the counters sum and the funnel
balance while one scored user was reported as skipped.

The counter keeps its name. Renaming it would make every `scoring_runs`
row written before the change unreadable against every row written
after, and the name is imprecise for only one of the three cases it
counts — the breakdown now says which.

A fourth funnel assertion holds the three to sum to
`users_skipped_no_cv`. It differs from the two oldest assertions in the
way that matters: all four counters increment inside the loop, on the
same branch, on the same pass, so it cannot balance while the loop
misattributes a cause.

**Exercised against real data**, not only in tests:
`python -m scripts.score_jobs --dry-run --user-id 9999` (a user id with
no profile row) reported `users_skipped_no_cv 1` broken down as
`no_profile 1`, with the fourth assertion holding non-trivially at
`1 == 1 + 0 + 0`. The ordinary run is unchanged: 3 users considered, 0
skipped, 3 scored, 294 pairs.

Worth writing down rather than rediscovering: `--user-id 9999` works as
a way of forcing that branch because **the flag does not check that the
user exists**. `select_target_user_ids()` returns `[user_id]` verbatim
when one is given, without consulting `profiles` — the profile lookup
happens inside the loop, which is exactly where the skip is classified.
That is harmless and useful: it is the only way to exercise the skip
path against real data while every real user is scorable.

### One thing added beyond the change as specified

`scripts/score_jobs.py` prints an explicit list of counters, so the
three new ones would have existed in the returned dict and in the
`scoring_runs` row while being invisible in the only tool a person reads
a scoring run with. Three print lines were added under
`users_skipped_no_cv`. Without them the change would have satisfied its
own tests and done nothing observable — §0.

### The archive leak

`.env` was present in the archive that was reviewed, so
`GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `ADZUNA_APP_KEY` and
`DATABASE_URL` have left the machine. **Rotation is a human task and is
not done.** It is the tenth incident, and like the nine before it, it
did not come from printing `.env`.

`scripts/pack.ps1` needs no change and was not changed. Its
`$ForbiddenPatterns` already covers `.env`, `.git/`, `storage/`, `*.zip`
and `*.pdf`, and it fails closed on all of them. **The archive was
simply not built with it** — which is the same failure Part 1 of this
document describes from the other direction: `pack.ps1` cannot protect
an archive that was made by some other means, just as it cannot include
a file that was never committed.
