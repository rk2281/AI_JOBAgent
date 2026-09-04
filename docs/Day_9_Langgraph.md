# Day 9 — consolidated report

What Day 9 built, what it got right, and the three things a later
review corrected. This does not replace `Day_9_Design_Note.md` (the
decisions and their reasoning) or `Day_9_progress.md` (what happened, in
order, with the numbers). It is the single view of where Day 9 stands
now that Day 10 Part 1 has closed the issues it left open.

Everything below marked **verified** was re-checked against the archive
independently of the Day 9 record.

---

## 1. What Day 9 actually was

The plan asked for eleven nodes: load profile, discover, validate,
deduplicate, analyze/normalize, retrieve, match, score, rank, explain,
decide notification.

**Nine of those eleven already existed and already ran.** Ingestion
validates and deduplicates inside `JobIngestionService`. Scoring
matches, scores, ranks and decides notification inside `run_scoring`.
Building any of them as a node would have produced a second
implementation of something with exactly one.

So Day 9 was reframed to: make the existing pipeline runnable as one
stateful, observable unit, with the branch points explicit instead of
implicit in a script's control flow. Eleven plan nodes became seven real
ones.

| Plan node | Day 9 node | Note |
|---|---|---|
| load profile | `resolve_targets` | validation folded in |
| discover jobs | `discover_jobs` | wraps `run_ingestion` |
| validate | — | inside `JobIngestionService` |
| deduplicate | — | `compute_content_hash` |
| — | `embed_jobs` | **added** — a gap in the plan |
| analyze / normalize | `enrich_jobs` | wraps `run_enrichment` |
| retrieve semantically | — | inside `run_scoring` |
| match / score / rank | `score_and_rank` | wraps `run_scoring` |
| explain | — | `match_reasons` column |
| decide notification | `decide_notification` | new, thin |
| — | `finalise` | terminal |

The governing rule: **a node is a unit of orchestration, not a unit of
computation.** Each collapsed step lives inside a function that already
owns a transaction and asserts its own funnel. Splitting `run_scoring`
into four nodes would mean either four transactions where there is one
commit per user, or four nodes sharing a session across node
boundaries — and `pairs_scored == jobs_scored × users_scored`, the one
assertion in this codebase that observes the loop rather than the plan,
would have had nothing left to assert over.

Reading the plan row literally was the main risk Day 9 avoided.

---

## 2. A process failure found before any code was written

`git status` at the start of Day 9 showed **HEAD contained the Day 8
migration but none of the Day 8 code.** The scoring tables were
committed; `job_scoring.py`, `repositories/scoring.py` and
`test_job_scoring.py` were not. HEAD had 19 test files, the working tree
had 20.

`pack.ps1` builds from `git archive`, which emits only the committed
tree. So every archive produced was a repository with scoring tables and
no scoring service — it would not import, and the 363-test baseline
could not be reproduced from it. `pack.ps1` was itself untracked: the
archive script was missing from every archive it built.

This was not a one-off. `CLAUDE.md` §1 already recorded that the
original do-not-fix list lived in `prompts/day8_open_issues.md`, "absent
from the archive it was reconstructed from". Same mechanism, still
armed.

Recorded in `CLAUDE.md` §3: a valid archive can still be a useless one.
`pack.ps1` asserts what it excluded, not what it should have included —
it cannot assert what was never committed.

**Context files fixed.** `.claude/commands/claude.md` was *moved* to
repo-root `CLAUDE.md`. Files under `.claude/commands/` are slash
commands; only the root file auto-loads. The file's own claim that it
"loads automatically at the start of every session" was false as placed,
and `guard.md`'s two references to "section 1 of `CLAUDE.md`" resolved
to nothing. Five paths were tracked individually rather than by
directory, and `prompts/` was deliberately not staged as a directory so
that a resurfaced `day8_open_issues.md` would have to be a deliberate
addition.

No `AGENTS.md` was created — that instruction is conditional on using a
tool that reads it, and nobody is.

---

## 3. Dependencies

| | Before | After |
|---|---|---|
| packages in `pip freeze` | 57 | 72 |
| `pip check` | no broken requirements | no broken requirements |
| tests | 363 passed | 363 passed |

`langgraph==1.2.11` added 15 packages with zero removals, zero upgrades,
zero downgrades. Every at-risk pin verified unchanged: `pydantic`
2.13.4, `pydantic_core` 2.46.4, `typing_extensions` 4.16.0, `tenacity`
9.1.4, `httpx` 0.28.1, `websockets` 16.1.1. The `google-genai` /
`websockets<17.0` pin was not touched.

---

## 4. One definition of "scorable user"

The problem: the graph needs to know whether a run is worth starting
*before* spending ingestion and enrichment quota, and `run_scoring`
answers that question only after doing all the work. Two independent
expressions of "scorable" would agree on the day they were written and
drift the first time either moved — invisibly, because both sides would
still produce a plausible count.

Three additions to `app/services/job_scoring.py`:

- `is_scorable_user()` — the rule, pure, whole truth table testable
  without a database
- `select_target_user_ids()` — was `_target_user_ids`, now public
- `resolve_scoring_targets()` — owns its session, returns ints only, so
  no ORM instance or lazy relationship crosses back into checkpointed
  workflow state

Behaviour unchanged, proven two ways. Before and after extraction:
`users_considered` 3 / 3, `users_skipped_no_cv` 0 / 0, `users_scored`
3 / 3, `jobs_scored` 98 / 98, `pairs_scored` 294 / 294.

And `scripts/scorable_targets_check.py`, an oracle that shares no code
with what it checks — oracle ids `[2, 3, 10]`, resolver ids `[2, 3, 10]`,
AGREE.

**The independence guard was itself verified**: adding the forbidden
import made
`test_scorable_targets_check_does_not_reference_the_shared_predicate`
fail as expected, and restoring left the script byte-identical. A test
that cannot fail is not a test.

*Verified independently:* `is_scorable_user` is called at
`job_scoring.py:307` and `:416`, and the denylist test forbids the
oracle from importing either it or `select_target_user_ids`.

---

## 5. The workflow

`app/workflows/` — `state.py`, `routing.py`, `nodes.py`, `graph.py` —
plus `scripts/run_agent.py`.

Design decisions worth carrying forward:

- **JSON primitives only in state.** No ORM objects, no datetimes as
  objects. This is what makes the state checkpointable.
- **`did_work` does not exist.** Status precedence is load-bearing
  instead, and `complete_no_work` outranks `dry_run`.
- **Skips live inside nodes, not on bypass edges.** Every node decides
  for itself whether to work and returns a skip *reason* when it does
  not, so eleven consecutive skipped enrichments read as eleven skips
  rather than as counters that never moved.
- **The straight run discover → embed → enrich → score is not
  conditional.** The sequence stays fixed and observable.
- **No `agent_runs` table on Day 9.** Every wrapped service already
  writes its own run row; what is unrecorded is the graph's own
  decisions, and Day 11 moves the node set, so a schema designed now
  would be migrated twice. The "we'll do it later and never do" risk is
  defused structurally: `build_run_summary(state) -> dict` is pure —
  reads no clock, touches no database — and returns exactly the fields
  an `agent_runs` row would hold. `finished_at` is stamped into state by
  `finalise` before the summary is built, so two calls on the same state
  are identical.

Alembic head unchanged: `9a4e7c1d5b82`, 8 migrations.

### Two decisions inspection reversed

- `app/agent/` → `app/workflows/`, because `CODEBASE_GUIDE.md` had
  already reserved the other name.
- `resolve_targets` no longer reads preferences. Inspection of the
  scoring loop showed preferences are not part of scorability — a user
  with no preference row is still scored, using defaults — so consulting
  them would have implied a gate that does not exist.

---

## 6. Tests: 363 → 471

*Verified independently: 471 collected, and the per-file split matches.*

| File | Tests |
|---|---|
| `test_workflow_state.py` | 31 |
| `test_workflow_routing.py` | 29 |
| `test_workflow_nodes.py` | 26 |
| `test_workflow_graph.py` | 16 |
| `test_job_scoring.py` | +6 |

Three tests failed on first run and each was a real finding:

1. Two graph-execution tests reached a live database, because
   `enrich_jobs` is the one stage a dry run still **calls** and it had
   not been stubbed.
2. `test_langgraph_is_imported_in_exactly_one_module` searched text
   rather than imports, and `__init__.py` mentions langgraph in prose.
   The test was wrong; the code was right. Rewritten to parse imports.

### The finding that would otherwise have been a vacuous test

`compiled.get_graph()` exposes `.nodes` and `.edges`. Walking them
revealed that **both notification branches deduplicate into a single
drawn edge**, because both currently target `finalise`. Reachability
therefore cannot be proven by reading edges. It is proven by executing
the graph with a stubbed `notify_eligible`: 3 → `notify_branch ==
"notify"`, 0 → the other branch.

Had this been assumed rather than checked, the reachability test would
have passed while proving nothing.

---

## 7. The first real run

Prediction recorded **before** the run: status `dry_run`; skips
`discover_jobs: dry_run` and `embed_jobs: dry_run`; computes
`enrich_jobs` and `score_and_rank`; persists nothing; 3 scorable users;
3 × 98 = 294 pairs; `notify_branch = no_qualifying`; `notify_eligible`
0.

`python -m scripts.run_agent --dry-run` matched **in every particular**.
Nothing was written — `recommendations` still held 98 rows at
`scoring_run_id [2]`.

Writing the prediction down first is what made "correct in every
particular" a result rather than a shrug.

Note `jobs_enriched` and `enrichment_remaining_null` print as `None`,
not `0`. The dry-run path returns before computing them, so they are
**absent, not zero** — the same distinction the signal columns on
`recommendations` make.

---

## 8. What Day 9 flagged rather than fixed

Three things, all now closed by Day 10 Part 1:

1. **`langsmith`** ships with `langchain-core` and activates on
   `LANGCHAIN_TRACING_V2` / `LANGSMITH_*`. Nothing in the repo sets or
   reads them — verified by searching the source.
2. **`httpcore2`, `httpx2`, `truststore`** were written into
   `requirements.txt` for the first time, as their own documented
   changeset rather than silently endorsed or unilaterally removed.
3. **`users_skipped_no_cv` conflates two states.** "No active CV
   version" and "active version not embedded" are indistinguishable to
   every caller, and only the second is fixable by running a stage.

---

## 9. What a later review corrected

Three items, for the record. Two of them are corrections to Day 9's own
conclusions.

**The dependency pins were not unexplained.** Day 9 recorded them as
"not Day 9 additions… not verified by this work". They are transitive
dependencies of the LangGraph stack:

```
langgraph -> langchain-core -> langsmith -> httpx2 -> httpcore2
                                                   -> truststore
```

`pip show httpx2` reports `Required-by: langsmith`, and `truststore` is
required by both `httpx2` and `httpcore2`. `starlette[full]` declares
`httpx2>=2.0.0` as a second path in, but that extra is not installed, so
it is latent rather than active. One `pip show` closed a question Day 9
deferred — and it means open issues 2 and 3 were one issue, since
`httpx2` is present *because* the telemetry client is.

**Searching the source did not prove what it needed to.** It proved the
repo does not set the tracing variables. It could not prove the
deployment host has not, and inspection expires the moment someone edits
a deploy config. Closed by `assert_tracing_disabled()`, which raises at
the top of `build_graph()` — fail-closed rather than a thing to
remember.

**Both AST layering tests had the same hole.** The `ast.Import` branch
read `node.names[0].name`, so `import langgraph` was caught and
`import json, langgraph` was not. The same hole was in
`test_no_workflow_module_imports_the_database_or_telegram`, which guards
a do-not-fix row. Fixed by one shared alias-complete parser, proved by
temporarily breaking `routing.py` — no workflow module has a
comma-separated import, so the fix was otherwise invisible from disk.

**`users_skipped_no_cv` was resolved without splitting the predicate.**
`is_scorable_user` is unchanged and still takes exactly two booleans;
`classify_skip_reason` runs only after it has returned `False`, so it
cannot change who gets scored. The counter name was kept — renaming it
would make every `scoring_runs` row written before the change
unreadable against rows written after.

---

## 10. Where Day 9 stands

Day 9's own scope is closed. All seven nodes exist, the graph runs end
to end against real data, the dry run writes nothing, `build_run_summary`
is pure and separately tested, and all three issues Part 7 carried
forward are now closed.

What Day 10 may assume: the graph runs end to end; `run_agent.py` exits
non-zero on `failed` or `degraded`, so a scheduler can tell a problem
from a quiet night without parsing output; quota exhaustion is
`degraded`, never a quiet success; a run that computed nothing reports
`complete_no_work`; every skipped stage carries a reason.

What Day 10 must **not** assume: that `notify_eligible > 0` is
achievable today (it is not — `weight_covered` 0.50 against a floor of
0.55, and the floor is a step function with three observed values, so
lowering it changes nothing); that `--dry-run` rehearses the pipeline
(it rehearses scoring only); that `jobs_enriched` being `None` means
zero.

**The one thing Day 9 could not fix and Day 11 depends on:** the notify
branch is proven reachable by test, not by data. Delivery will be
attached to a path that has never fired on real rows, and what would
change that is upstream — the abstention asymmetry, which Design Note
§10 deliberately declined to decide under time pressure. That reasoning
was right; the decision is still outstanding.