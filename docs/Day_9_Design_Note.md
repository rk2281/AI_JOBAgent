# Day 9 — LangGraph Agent Workflow: the decisions

This is the file `CLAUDE.md` §7 points at. It records the decisions
taken before any code was written, the reasoning behind each, and the
inspections that changed a decision after it had already been made.

Written against head `9a4e7c1d5b82`, 8 migrations. Test count at the
start of Day 9: **363**.

---

## Part 1. What Day 9 is, and what it is not

The plan spreadsheet's Day 9 row asks for eleven nodes: load profile,
discover, validate, deduplicate, analyze/normalize, retrieve, match,
score, rank, explain, decide notification.

**Nine of those eleven describe work that already exists and already
runs.** Ingestion validates and deduplicates inside
`JobIngestionService`. Scoring matches, scores, ranks and decides
notification inside `run_scoring`. Rebuilding any of it as a node would
produce a second implementation of something that currently has exactly
one.

So Day 9 is: **make the existing pipeline runnable as one stateful,
observable unit, with the branch points explicit instead of implicit in
a script's control flow.** That is a smaller job than the row implies,
and saying so is what stops an agent reading the row literally and
reimplementing scoring inside a node.

Eleven planned nodes map onto seven actual ones:

| Plan node | Day 9 node | Note |
|---|---|---|
| load profile | `resolve_targets` | validation folded in, see §3 |
| discover jobs | `discover_jobs` | wraps `run_ingestion` |
| validate | — | inside `JobIngestionService` |
| deduplicate | — | `compute_content_hash` |
| — | `embed_jobs` | **added**, see §4 |
| analyze / normalize | `enrich_jobs` | wraps `run_enrichment` |
| retrieve semantically | — | inside `run_scoring` |
| match / score / rank | `score_and_rank` | wraps `run_scoring` |
| explain | — | `match_reasons` column |
| decide notification | `decide_notification` | new, thin |
| — | `finalise` | terminal |

**A node is a unit of orchestration, not a unit of computation.** The
collapses are why: each collapsed step lives inside a function that
already owns a transaction and asserts its own funnel. Splitting
`run_scoring` into four nodes would mean either four transactions where
there is one commit per user, or four nodes sharing a session across
node boundaries. And `pairs_scored == jobs_scored × users_scored` — the
only assertion in this codebase that observes the loop rather than the
plan — would have nothing left to assert over.

---

## Part 2. Where the code lives, and the decision that was reversed

### 2.1 `app/workflows/`, not `app/agent/`

The first proposal was a new `app/agent/` package. That was **wrong and
was reversed**, on inspection: `app/workflows/` already exists as an
empty reserved namespace, and `CODEBASE_GUIDE.md` names its intended
purpose as "scheduled matching runs". A new `app/agent/` beside it would
have created two names for one orchestration layer and left the reserved
package empty forever.

### 2.2 Why langgraph is not in `app/integrations/`

`CLAUDE.md` §4 says `app/integrations/` is the only place a third-party
**service** SDK is imported. The literal reading would put every
langgraph import behind a wrapper there.

Every current occupant of that directory — `adzuna.py`, `gemini.py`,
`gemini_embeddings.py`, `gemini_enrichment.py`, `http_errors.py` — is a
network client holding credentials with a quota. That directory is a
swap boundary for vendors that can fail over a wire, not a quarantine
for the word "third-party". langgraph makes no network call, holds no
credential and has no quota; it sequences local function calls, exactly
as `sqlalchemy` sequences local SQL.

**The rule's wording is therefore sharpened to: anything that makes a
network call on someone else's credentials.** §4 already said "vendor
network clients, not every third-party package"; this states the test
rather than the category.

A test asserts langgraph is imported in exactly one module, by parsing
imports rather than searching text — `__init__.py` explains in prose why
langgraph lives here, and prose is not a dependency.

---

## Part 3. Profile loading is validation, not a node

`load_profile` is not a separate node. Three reasons:

1. `run_scoring` already skips CV-less users per user and counts them in
   `users_skipped_no_cv`. A `load_profile` node would either duplicate
   that rule or hold a `Profile` ORM object in state.
2. The graph needs exactly one thing from profiles — *is there anybody
   worth running for* — and that is one question, not a stage.
3. With `user_id=None`, `select_target_user_ids` selects every user with
   a profile, so a per-user "no profile → stop" edge is meaningless for
   the all-users run.

### 3.1 One definition of "scorable user"

The graph must not stop on a definition of scorable that differs from
the one scoring applies. So `is_scorable_user()` was extracted, and
**both** `run_scoring`'s loop and `resolve_scoring_targets()` call it.
Same move as Day 8's extraction of `is_notify_eligible` and
`select_status`, for the same reason.

Preferences are **not** part of scorability. A user with no preference
row is still scored, using defaults. `resolve_targets` therefore does
not read preferences at all — doing so would imply a gate that does not
exist. This was found by inspection, after an earlier draft had the node
calling `UserRepository.get_preferences`.

### 3.2 The circularity problem, and the two oracles

Because both callers share one predicate, **comparing them to each other
proves nothing** — they would agree even if the predicate were wrong.
Two independent oracles were used instead:

- **Oracle 1, independent in implementation.**
  `scripts/scorable_targets_check.py` reaches the answer through raw SQL
  written from the schema, importing neither `is_scorable_user` nor
  `select_target_user_ids`. This is the same exception
  `scoring_isolate.py` carves out for its hand-computed cosine
  similarity: "verification math, not a second implementation." A test
  parses that script and fails if it ever imports the predicate — which
  would make its agreement circular while it went on printing `AGREE`.
- **Oracle 2, independent in time.** The characterization values were
  captured from `run_scoring(dry_run=True)` **before** any edit:
  `users_considered 3, users_skipped_no_cv 0, users_scored 3,
  jobs_scored 98, pairs_scored 294`. Re-run after the extraction:
  identical. Shared code cannot defeat an oracle captured before the
  shared code existed.

The comparison against `run_scoring` is **conditional on
`jobs_scored > 0`**, because `run_scoring` enters its user loop only
then. Asserting it unconditionally would report an empty jobs table as a
target-resolution failure.

### 3.3 The four states, two of which look identical

`active_version_with_embedding()` joins through
`profiles.active_cv_version_id` and filters `embedding IS NOT NULL`. A
user is therefore in one of four states:

1. no profile row
2. profile, but `active_cv_version_id IS NULL`
3. profile and active version, but that version has no embedding
4. profile and an embedded active version — the only scorable one

**States 2 and 3 are indistinguishable to every caller.** Both return
`None`; `run_scoring` folds them into one `users_skipped_no_cv`. Only
state 3 is fixable by running the embedding pass. This is pre-existing,
not introduced by Day 9, and it is **not** to be fixed by splitting the
predicate — that would make the graph's definition differ from scoring's.
It is recorded as an open issue and `scorable_targets_check.py` prints
all four separately, which is currently the only place the distinction
is visible. Today: `0 / 0 / 3`.

---

## Part 4. `embed_jobs` is mandatory, and the plan is wrong

Embedding is absent from the plan's Day 9 row. It is in the graph
anyway, and this is a **plan gap, not a code gap**.

`run_scoring` skips every job whose embedding is NULL and counts it in
`jobs_skipped_no_embedding`. A graph that ingested twenty jobs and then
scored without embedding in between would score none of them — and
`pairs_scored == jobs_scored × users_scored` would balance perfectly
while it happened.

A test walks **every path** from `discover_jobs` to `score_and_rank` and
fails if any of them misses `embed_jobs`. It fails on deletion and on
rewiring, which a "the node exists" assertion would not.

### 4.1 Embedding before enrichment

`build_job_document(title, description)` reads **only** title and
description. Enrichment output — skills, work_mode, experience bounds —
never reaches a vector. So the two stages are independent and the
ordering was free to choose.

It is chosen on quota grounds: enrichment burns a daily allowance and
stops mid-loop on a 429, while embedding is what makes newly ingested
jobs scorable at all. Embedding first means one quota error cannot make
a whole ingest invisible to scoring. Reversed, it could.

---

## Part 5. State

### 5.1 JSON primitives only

Everything at a node boundary is `str | int | float | bool | None | list
| dict`. No dataclass, no enum, no ORM instance, no session, client or
callable. Two reasons:

1. LangGraph checkpoints state. A state holding 99 job descriptions and
   768-dimension vectors is a serialisation problem nobody asked for.
2. Every service owns its own transactions. Passing rows between nodes
   would mean holding SQLAlchemy instances across session boundaries —
   detached instances, a class of bug this codebase does not have and
   should not acquire in order to draw a graph.

**Nodes talk to each other in counters, and to the database in rows.**
`run_scoring` writes 98 recommendation rows to Postgres; if those rows
also travelled through state there would be two copies of the ranking
and no test that they agree.

The three services return three different shapes — `IngestionResult`
with an enum status, `EmbeddingResult` with an enum status and a nested
counters dataclass, and two plain dicts. Three normalisers are where
that becomes one shape. `call_seconds` is dropped from enrichment (a
~97-entry float list that informs no decision) and replaced by its
total.

### 5.2 Why `did_work` does not exist

An earlier draft had one `did_work` boolean. It was wrong in exactly the
shape §0 warns about: a dry run computes every signal on every pair and
deliberately writes nothing, while a quota-stopped run writes nothing
because it did nothing. One boolean reports those identically.

Four lists and two derived booleans instead:

| Field | Means |
|---|---|
| `stages_attempted` | entered its work path and called its service |
| `stages_skipped` | `"name: reason"`, never merged with the above |
| `stages_computed` | the service actually computed over domain data |
| `stages_persisted` | durable rows were written |
| `computation_performed` | `bool(stages_computed)` |
| `persistence_performed` | `bool(stages_persisted)` |

A skipped stage always carries **why**. `"enrich_jobs"` alone would be
indistinguishable from a stage that ran and found nothing; if Day 10's
scheduler skips enrichment on eleven consecutive nights, that must read
as eleven skips with a reason.

### 5.3 Status precedence, which is load-bearing

```
failed
  → no_scorable_users / no_candidate_jobs
    → degraded
      → complete_no_work
        → dry_run
          → complete_no_qualifying
            → complete
```

`degraded` outranks `complete_no_work` because a quota-stopped
enrichment and an idle night both produce small numbers and only one is
a problem — reporting it as "nothing to do" is the
`complete_no_qualifying` mistake one layer up.

`complete_no_work` outranks `dry_run` because "computed nothing" is the
more important fact than "was not going to write anyway".
`writes_prevented` stays in the summary either way, so dry-run-ness is
never lost — it is just not the headline.

---

## Part 6. Skips inside nodes, not bypass edges

Every node always executes and decides internally whether to work. An
edge that routes *around* a node leaves nobody to write the skip entry —
the graph-level version of a silently excluded row. It also cuts the
edge count from eleven to three, and a typo'd edge target is the failure
LangGraph reports only at run time, on the branch that is never taken.

Three conditional edges: after target resolution, after scoring, and the
notification branch.

Note what does **not** stop the run. `ALL_ABSTAINED` and `DEGENERATE`
both describe a ranking carrying no information, and both still reach
`decide_notification`, because both wrote rows and a run that wrote rows
has made a decision. They surface as `degraded`. Routing them to
`finalise` would hide a bad ranking behind the same path as an empty
jobs table.

---

## Part 7. Dry-run semantics

Only two of the four services take `dry_run`.

| Stage | Under `--dry-run` | Why |
|---|---|---|
| `discover_jobs` | **skipped**, reason `dry_run` | `run_ingestion` has no such parameter; calling it would hit Adzuna and insert rows |
| `embed_jobs` | **skipped**, reason `dry_run` | no such parameter; would spend embedding quota |
| `enrich_jobs` | called with `dry_run=True` | counts candidates, makes no API call |
| `score_and_rank` | called with `dry_run=True` | full computation, writes nothing |

**`--dry-run` is a scoring rehearsal, not a pipeline rehearsal.** It
scores whatever is already in the database. Reading it as "what tonight's
run would do" is wrong by exactly the number of jobs ingestion would have
added, and the runner's docstring says so.

---

## Part 8. No `agent_runs` table on Day 9

Option A was chosen. Every wrapped service already writes its own run
row, so nothing about the *work* is unrecorded; what is unrecorded is
the graph's own decisions, and on Day 9 those are read by a person
watching a script print them.

The argument that settled it: Day 11 adds a notification node, so a
schema designed now would be migrated again in two days, guessing
columns for a graph whose node set is still moving.

The "we'll do it on Day 10 and never do" risk is defused structurally:
`build_run_summary(state) -> dict` is pure and separately tested, and
returns exactly the fields an `agent_runs` row would hold. Day 10's
migration persists a dict that already exists.

**Purity is enforced by one rule: it reads no clock and touches no
database.** `finished_at` is stamped into state by the `finalise` node
before the summary is built, so two calls on the same state are
identical. A summary that consults the time cannot be compared against
itself.

Alembic head is unchanged: `9a4e7c1d5b82`, 8 migrations.

---

## Part 9. The notify branch, and why it was probed first

Day 9 ends with `decide_notification` having two outgoing branches, one
of which **cannot execute with today's real data**. Day 10 schedules the
graph. Day 11 hangs Telegram delivery off the branch that has never run.
The first time it executes for real, it will be sending a message to a
person.

### 9.1 The probe

`scripts/notify_reachability_probe.py` — read-only, no re-scoring, no
settings mutation. Every input to the three gates is already stored per
row (`final_score`, `semantic_raw`, `weight_covered`), so the
counterfactual is arithmetic over 98 existing rows.

It does not override settings. `is_notify_eligible` reads
`settings.semantic_notify_floor` and
`settings.min_weight_covered_to_notify` off the module global at call
time, so the only way to make it evaluate a different floor is to mutate
a global every importer shares — a probe that mutates process-wide
config to measure something can change what it measures. It evaluates
the gates locally and **cross-checks that local evaluation against the
real `is_notify_eligible()` at the unmodified floor for all 98 rows**,
refusing to print its table on any disagreement. Result: 0
disagreements.

**Prediction recorded before the run:** 1–4 eligible candidates at a
relaxed coverage floor, not dozens.

### 9.2 Result

`scoring_run_id 2`, `user_id 2`, 98 rows, thresholds `0.7 / 0.62 / 0.55`.

`weight_covered` takes only three values: `0.35` ×23, `0.50` ×73,
`0.80` ×2.

| floor | pass_final | pass_sem | pass_cov | **ALL THREE** |
|---|---|---|---|---|
| 0.55 ← current | 3 | 16 | 2 | **0** |
| 0.50 | 3 | 16 | 75 | **2** |
| 0.45 | 3 | 16 | 75 | **2** |
| 0.40 | 3 | 16 | 75 | **2** |

Rows failing exactly one gate while passing the other two: at 0.55,
**2** blocked by coverage alone; at 0.50 and below, **11** blocked by
final score alone and **1** by semantic alone.

**Prediction correct, at the low end: 2.**

Three findings:

1. **The coverage floor is a step function, not a dial.** Coverage has
   three observed values, so anything in (0.35, 0.50] gives 2 and
   anything in (0.50, 0.80] gives 0. **0.45 and 0.40 are no-op settings
   today** and must not be presented as meaningful tuning.
2. Coverage is what binds today — exactly 2 rows are blocked by it alone.
3. Once coverage stops binding, **final score is materially more
   constraining than semantic**: 11 rows blocked solely by it against 1
   solely by semantic. Not "semantic is a close second" — that would be
   arguing from `semantic_raw`'s maximum of 0.6928 sitting near its 0.62
   floor, when the binding analysis says otherwise.

**No threshold was changed, and none should be changed on this
evidence.** The probe is diagnostic. Enrichment is precisely what moves
the coverage distribution, so the probe should be re-run after the first
full enrichment pass.

### 9.3 How the branch is proven instead

By **executing** the graph with a stubbed `run_scoring` returning
`notify_eligible: 3`, and asserting the run ends with
`notify_branch == "notify"`. Then `0`, and the other branch.

It cannot be proven by reading edges: both branches currently point at
`finalise`, and LangGraph draws them as a single edge. The wiring is
asserted separately against `NOTIFICATION_PATH_MAP`, which Day 11
changes by one value.

---

## Part 10. What this note deliberately does not cover

**The abstention asymmetry.** An abstaining signal leaves the
denominator while a 0.0 stays in it, so missing data can outrank bad
data. Nothing in the graph depends on it — the graph calls `run_scoring`
and reads counters, and the asymmetry lives inside `combine()`. It needs
a decision, not a patch, and deciding it under time pressure because it
came up in the same week is how it gets patched instead.

**Enrichment itself.** The `abstain_experience ≈ 60` prediction is
untouched. The graph only needs to route correctly when enrichment
produces little.

---

## Part 11. The four decisions, as settled

1. **langgraph import location** — `app/workflows/`, and the §4 wording
   sharpened to "anything that makes a network call on someone else's
   credentials".
2. **`embed_jobs` in scope** — yes, mandatory, with a path test. Recorded
   as a plan gap.
3. **`agent_runs` table** — not on Day 9. `build_run_summary` is the
   seam.
4. **The reachability probe** — built, run, and its result recorded in
   §9. Prediction was correct.
