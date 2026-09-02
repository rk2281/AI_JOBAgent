# Day 8 — Matching and Scoring

A record of what Day 8 built, why each choice was made, what went
wrong, and what Day 9 can rely on.

---

## Part 1. What Day 8 was for

Day 7 left 99 job postings and 3 CV versions, every one of them
embedded, and a working similarity search. Asking "which jobs are
closest to this CV" already had an answer.

Day 8's job was to turn that answer into a **score** — one number per
candidate-job pair, built from five signals, ranked, explainable, and
stored.

The distinction matters. Day 7 could tell you a job was near your CV in
vector space. It could not tell you the job wanted eight years of
experience you do not have, or that it is in a city you did not ask
for, or that it was posted by a staffing agency rather than the
employer. Semantic similarity is 20% of the answer. Day 8 is the other
80%, plus the machinery that keeps all five parts honest when some of
them have no data.

---

## Part 2. The concepts, from scratch

### 2.1 What a weighted score is

Five separate judgements have to become one number. Each signal returns
a value between 0 and 1, and each has a weight:

| Signal | Weight | Question it answers |
|---|---|---|
| Skill match | 30% | Does this person have the skills the posting asks for? |
| Semantic similarity | 20% | Is this job about the same kind of work as this CV? |
| Experience match | 20% | Does this person have the years the posting wants? |
| Location match | 15% | Is this job where the person wants to work? |
| Title / role match | 15% | Does the job title resemble the roles they are targeting? |

The weights sum to 1.0, and a validator in `app/core/config.py` refuses
to construct `Settings` if they do not. That check has been proven to
fire: `scripts/config_selftest.py` deliberately builds a broken
`Settings` and requires the `ValueError`. A check that has never fired
is not known to be a check.

### 2.2 Abstain, and why it is not zero

The central idea of Day 8, and the one everything else is built around.

A signal can be in three states, not two:

- **It looked and found a good match** → a high value.
- **It looked and found nothing** → 0.0.
- **It could not look** → abstain, represented as `None`.

The third case is the one that is easy to get wrong. If a job posting
lists no skills, the skill signal has nothing to compare against. The
tempting move is to score that 0.0 and move on. That is wrong, and the
reason is worth stating plainly: **a data gap is not a fit gap.**

If missing skills scored 0.0, every job whose posting happens to be
badly written would sink below every job whose posting happens to be
detailed — for a reason that has nothing to do with whether the person
would be good at it. The ranking would be measuring the quality of job
adverts, not the quality of matches.

So a signal with no data returns `None`, and `None` means "remove my
weight from the calculation entirely".

### 2.3 Renormalisation, and `weight_covered`

If the skill signal abstains, its 30% is removed from both the top and
the bottom of the fraction. The remaining four signals are weighted
against 0.70 instead of 1.00.

```
weight_covered = sum of the weights of the signals that DID answer
weighted_total = (sum of weight × value, for answering signals) / weight_covered
```

`weight_covered` is not thrown away after use. It is stored on the
`recommendations` row, because **a score built from 35% of the model is
not comparable to a score built from 100% of it**, even when the two
numbers are identical. Without that column, two rows reading 0.82 would
be indistinguishable, and one of them would be a confident number
computed from almost nothing.

### 2.4 Why a score of zero has two meanings

`final_score = 0.0` can mean two completely different things:

- Every signal answered, and every one of them answered zero. A real,
  measured, total mismatch.
- No signal could answer at all. `weight_covered` is 0.0 and the score
  is the arithmetic default, not a measurement.

These need to be distinguishable from the stored row alone, without
re-running anything. So when `weight_covered == 0.0`, `combine()` puts
`"no signal had data"` as the **first** entry in `match_reasons`. The
row explains itself.

### 2.5 Rescaling semantic similarity, and why fixed anchors

Day 7 ended with a measured problem. Across one AI/ML CV and all 99
jobs, raw cosine similarity ran from **0.5058 to 0.6928** — a spread of
0.187, with roughly half the corpus bunched between 0.59 and 0.65.

At a 20% weight, a 0.187 spread contributes at most 3.74 points out of
20. A signal nominally worth 20% was behaving like 4%.

The fix is to rescale: map 0.50 → 0.0 and 0.70 → 1.0, and clamp outside
that. The same data then spans 0.58 to 19.28 points.

The important decision was **where the anchors come from**.

*Rejected: min-max within the candidate set.* Rescale against the best
and worst job seen in this particular run. The obvious objection is
that the best match always scores 1.0 even when it is terrible. The
stronger objection is that `recommendations` is a **stored** table. A
score computed relative to that day's neighbours can never be
reproduced, because it was never a property of the pair — it was a
property of the batch. Tomorrow's ingestion would silently change
yesterday's scores.

*Chosen: fixed anchors in config* — `semantic_anchor_low = 0.50`,
`semantic_anchor_high = 0.70`. The number depends only on the two
things being compared. It is reproducible, and when the anchors are
retuned that is a versioned, visible change.

### 2.6 Quality as a multiplier, not a sixth signal

Some postings are less trustworthy descriptions of a job than others. A
staffing agency posting is one step removed from the employer. A
posting whose location is just "India" is hiding something the reader
needs.

These are **not** signals, and the distinction is not pedantic. A
signal answers *"does this person fit this job?"*. These answer *"is
this posting a trustworthy description of a job at all?"*. Mixing the
two would let a trust problem masquerade as a fit problem.

So they are a multiplier applied after the weighted total:
`quality_multiplier_agency = 0.90`, `quality_multiplier_no_city = 0.95`,
and both together multiply to 0.855.

They are penalties, not filters, deliberately. **A filtered job is not
ranked low — it is absent**, and absence is invisible.

### 2.7 Boundaries, and why every one is inclusive

Day 6 lost time to a threshold written `median < 500` that stayed
silent when the median was exactly 500. The boundary is the case that
fails while looking like it should pass.

So every threshold in Day 8 is written `>=`, and every one is tested at
its exact value rather than near it:

- Raw similarity of exactly 0.70 maps to 1.0 **without** being counted
  as clamped, so the clamp counters use strict `>` and `<`.
- The notify floor uses `>=`, so exactly 0.62 clears.
- `x == job_min` and `x == job_max` both score exactly 1.0 on
  experience.

---

## Part 3. The decisions

### 3.1 Where the weights came from — and an unresolved provenance problem

The five weights are 30/20/20/15/15. They are enforced by a validator
and frozen as `weights_version = 1`.

**Their source could not be confirmed.** The working notes for this day
say the weights come from a plan spreadsheet's "Matching & Scoring"
tab, which is authoritative, and that the original context document
"dropped the whole 15% Title/Role signal and invented a freshness
signal that does not exist."

The plan document was located and read. It contains **no numeric
weights at all** — its section 10 lists the signals and says only that
"the weights should remain configurable". It also explicitly names
**"Job-title match"** among the signals, and mentions no freshness
signal anywhere. Its Day 8 row reads: *"Skill, experience, location,
title scoring + semantic similarity + ranking."*

So the claim in the working notes is not supported by the document that
was found. Either the spreadsheet is a separate file that has not been
located, or the weights were chosen during Day 8 and the spreadsheet
attribution attached itself to that memory afterwards.

This is recorded as unresolved rather than tidied away. The weights
themselves are unaffected — they are internally consistent, they sum to
1.0, and they are versioned. What is unproven is the story about where
they came from, and an unproven story written down as fact is worse
than an admitted gap.

Two smaller divergences from the plan document, both deliberate:

- The document lists **"User preference match"** as a separate signal.
  It was folded into location and title instead, since
  `preferred_locations` and `target_roles` are exactly what those two
  scorers consume. A sixth signal reading the same inputs would have
  double-counted them.
- The document's example uses a notification threshold of **80%**. The
  code defaults to **0.7**, matching `UserPreference`'s own column
  default, so that a user who never sets preferences scores exactly as
  an onboarded user who accepted every default would.

### 3.2 Company matching is exact, not substring

Staffing agencies are detected by comparing a normalized company name
against a configured list.

*Rejected: substring matching.* It fires "meta" against "Metadata
Solutions" and penalises the wrong job, with nothing reporting that it
happened.

*Chosen: exact normalized equality.* This misses real variants —
"Vrinda International Pvt Ltd" will not match "Vrinda International" —
and that miss shows up as a lower `quality_penalty_agency` count, which
is a number a person can read and question.

**A loud miss beats a silent false positive.**

One known case this cannot catch: a single `company` field holds three
companies joined by `||`. Exact-match lookup will never see it.

### 3.3 Three notification gates, not one

A pair is eligible to notify only if **all three** hold:

1. `final_score >=` the user's threshold
2. `semantic_raw >=` `semantic_notify_floor` (0.62)
3. `weight_covered >=` `min_weight_covered_to_notify` (0.55)

The first is obvious. The second stops a pair from clearing the bar on
location and title alone while being semantically unrelated to the CV.

The third is the one that is easy to leave out, and it is the one that
proved its worth on the first real run — see Part 6. Renormalisation
means a threshold of 0.7 does not mean the same thing for a job scored
on 35% of the weight as for one scored on 100%. Without the third gate,
a data gap reads as a good match.

### 3.4 One job per enrichment call, never batched

Day 7 batched embeddings eight at a time and caught a model returning
one vector for eight inputs, because a returned count can be compared
to an input count.

The generation equivalent cannot be caught that way. A model returning
objects out of order produces JSON that parses cleanly and attaches one
job's skills to another job's row. Nothing raises. Nothing logs.

So `job_id` travels **in** the prompt and is compared before anything
is written, and jobs are enriched one at a time.

### 3.5 Attempts ceiling instead of a binary filter

Day 7's embedding pass filtered on `attempts == 0` — one failure and
the row was out. That was right there, because those failures were
deterministic: a dimension mismatch fails identically every time.

Enrichment's failure mode is **variance**, not determinism. Fifteen
timed calls during isolation ran from **7.4s to 74.1s** for requests
that never changed, and the spread was not driven by request size — the
smallest call in the set was repeatedly among the slowest.

A row that times out is very often merely unlucky. A binary filter
would lock a merely-slow row out of every future run permanently. So
`enrichment_max_attempts = 3`, the timeout was raised from 45s to 90s,
and `--dry-run` prints a **time range**, never a single number, because
one number advertises a precision the measurement has already
disproved.

### 3.6 The skills denominator problem

Skill score is `|job skills ∩ candidate skills| / |job skills|`.

A live call returned "Strong communication skills" alongside seven real
technologies. No CV will ever list that as a skill, so it silently
lowers every good candidate's 30% signal — 0.625 instead of 0.714 —
with nothing anywhere reporting it.

Soft-skill and word-count filters now drop these, and **every drop is
counted**. "Amazon Web Services" is exactly three words and must be
kept, so the comparison is `> MAX_SKILL_WORDS`, not `>=`.

---

## Part 4. What went wrong

### 4.1 The limit bug: a real job dropped from every run, invisibly

`JobRepository.nearest_to()` filters `is_active` and
`embedding IS NOT NULL`. It does **not** filter `is_excluded` —
deliberately, because it is also Day 7's search method and two other
callers depend on its scope.

`run_scoring()` was calling it with `limit = jobs_scored`, and
`jobs_scored` is the count of scorable jobs, which **does** exclude
excluded rows.

So the limit was smaller than the pool it was drawn from. With 99
embedded jobs and one excluded, the query asked for the nearest 98 out
of 99 — and the excluded job took one of those 98 slots. The service
skipped it, and the 98th-nearest real job was pushed off the end of the
window. Never scored, never ranked, never counted.

**Both existing funnel assertions balanced perfectly while this was
happening:**

```
jobs_considered            = 99
jobs_skipped_no_embedding  =  0
jobs_excluded_manual       =  1
jobs_scored                = 98
99 == 0 + 1 + 98   ✓
```

They balanced because every number in them is computed from repository
counts taken **before** the loop runs. They describe what the run
*intended* to do. Nothing checked whether the loop kept that promise.

The fix had three parts, and all three were required:

- **The limit** — a new repository method,
  `count_active_embedded_jobs()`, counting the pool `nearest_to()`
  actually searches. Counted independently rather than derived as
  `count_active_jobs() - count_active_missing_embedding()`, because a
  subtraction can never disagree with itself; two queries that *can*
  disagree are the entire point.
- **`ef_search`** — it scaled to `jobs_scored`. Once the limit became
  the larger pool number, an `ef_search` below the limit would make
  pgvector's HNSW return fewer rows than asked for, with no error. At
  99 rows the planner picks a sequential scan and this never bites; it
  would begin biting silently once the table outgrew that, which is the
  worst possible time to find out.
- **A third funnel assertion** —
  `pairs_scored == jobs_scored × users_scored`. This is the only one of
  the three that can fail because of something the *loop* did rather
  than something the *plan* said.

The single `if job.is_excluded or job.id not in jobs_by_id: continue`
was also split into two branches with two counters. One condition is
expected; the other can only fire if two reads in two different
sessions disagree about which jobs exist. Folding both into one silent
`continue` is what let the bug hide.

Measured after the fix: `pairs_scored 98`, `jobs_scored 98`,
`users_scored 1`, `nearest_dropped_excluded 1`,
`nearest_dropped_unscorable 0`.

### 4.2 The provider's response body leaked into a database column

`describe_genai_error()` was writing the provider's full error body
into `skills_extraction_error`. A row was found containing
`RateLimitError | Error code: 429 - {'error': {'message': ...`.

It now returns class name and HTTP status only, and a test asserts the
message text is absent.

### 4.3 `httpx` was logging request URLs at INFO

`httpx` and `httpcore` were set to WARNING inside a setup function that
only `app/main.py`'s lifespan calls. Scripts never run that lifespan,
so the setting never applied and `httpx` logged every request URL.

Harmless for google-genai, which sends its key as a header. **Not**
harmless for Adzuna, whose `app_id` and `app_key` are query parameters.
A single `python -m scripts.ingest_jobs` would have printed both.

The levels are now set at **import** of `app.core.logging`, not inside
a function.

### 4.4 A quota failure was spending the attempts budget

`gemini_enrichment.py` had no `retry_options`, unlike `gemini.py` and
`gemini_embeddings.py`, which both exclude 429 from retry. Each 429 was
retried four times; three jobs spent twelve calls against an
already-exhausted quota.

Worse, a quota failure called `mark_enrichment_failed()`, incrementing
`skills_extraction_attempts`. A protection against bad rows had become
a hazard to innocent ones — rows were being pushed towards the ceiling
for a reason that was not about them.

A quota error now breaks the run and does not touch the attempts
counter. Verified live: after a run that hit a 429, `errored` stayed at
3 rather than rising.

### 4.5 A script was accused of dropping rows. It was not.

`scripts/query.py` appeared to be losing output. The missing lines were
a truncated paste. Two independently computed numbers — `rows fetched`
and `rows rendered` — were added anyway, and they settled the question
in one round the next time it came up.

---

## Part 5. How it was verified

### 5.1 The migration

Migration `9a4e7c1d5b82`, hand-written,
`down_revision = 'f2c81b3ea774'`. Verified down-up-down-up twice, with
`python -m scripts.check_indexes` after **every single step** — five
times, not just first and last. Both HNSW indexes present every time.
Defaults landed on all 99 rows.

`weight_covered NOT NULL DEFAULT 0` was only safe because
`recommendations` was empty, which was verified by query rather than
assumed.

### 5.2 The isolate script imports what runs

`scripts/enrichment_isolate.py` **imports** `ENRICHMENT_SCHEMA` and
`ENRICHMENT_PROMPT` from `app/integrations/gemini_enrichment.py`
rather than keeping a copy, and this was verified by identity (`is`),
not by reading both. What was proven against live calls is exactly what
runs against the 99 stored rows.

### 5.3 The tests

**363 passing.** 24 of them new in `tests/test_job_scoring.py`, added by
extracting three pieces of logic out of `run_scoring()` — the status
ladder, the notify gate, and the funnel equalities — into pure
functions that need no database, no event loop, and no
`pytest-asyncio`.

The extraction was proven behaviour-preserving by re-running the same
dry run before and after and comparing `score_min` and `score_max` to
full floating-point precision: `0.011566162109382109` and
`0.685686159133921`, identical in every digit.

Three of the new tests are worth naming:

- **The ordering test.** A fully abstained run has `weight_covered = 0`
  on every pair, so every score is 0.0, so `distinct_score_count` is 1
  — it satisfies the `DEGENERATE` condition perfectly. `ALL_ABSTAINED`
  must be checked first, or the exact failure this enum was built to
  name gets filed under the wrong name.
- **The unreachable-status test.** `PARTIAL` and `FAILED` are never
  returned by the status ladder. That is pinned as a known fact so that
  adding partial-failure handling later fails this test and forces the
  ladder to be updated with it.
- **The limit-bug test.** It asserts that the two original funnel
  equalities *balance* on the broken numbers, and that the third one
  does not. The test's purpose is to record why the third exists.

### 5.4 The arithmetic was checked by hand

`score_min` was recomputed independently:
`(0.505783081054691 − 0.50) / 0.20 × 0.20 ÷ 0.50 = 0.011566162109382`,
matching the reported value to fifteen digits. The top-ranked pair was
checked the same way.

---

## Part 6. The result

The first real scoring run against 99 jobs and a real profile.

```
status                    complete_no_qualifying
jobs_considered           99
jobs_excluded_manual       1
jobs_scored               98
pairs_scored              98
distinct_score_count      98
notify_eligible            0

abstain_semantic           0
abstain_skill             96
abstain_experience        98
abstain_location          23
abstain_title              0

semantic_raw_min       0.5058
semantic_raw_median    0.5911
semantic_raw_max       0.6928

score_min              0.0116
score_median           0.2040
score_max              0.9835
```

**The ranking works.** Rank 1 was "Applied AI Solutions Architect".
Rank 98 was "Sales Promoter", with "Medical Representative" and
"Producer (Brut Tamil)" just above it. For an AI/ML CV, that is the
right shape.

**The third notify gate earned its keep on the first run.** The top job
scored **0.983** against a threshold of 0.7. It cleared the first gate
easily and cleared the semantic floor. It was refused because
`weight_covered` was 0.50, below the 0.55 minimum. That 0.983 was a
confident-looking number computed from half a model, and the gate that
exists specifically to catch that caught it.

**Half the model is inactive, and the counters are the only thing that
says so.** `abstain_experience` is 98 of 98 — the entire 20% signal.
`abstain_skill` is 96 of 98. Only 5 jobs have ever been enriched, and
of those only 2 produced any skills.

This is the failure the abstain design makes possible, and it looks
exactly like health: a full funnel, every column balanced, every pair
written, a varied ranking, and a status of `COMPLETE_NO_QUALIFYING`
which is documented as the healthy quiet day. Nothing about the shape
of the run says anything is wrong. Only the abstain counters do.

**The renormalisation has a consequence that was not anticipated.**

With skill and experience both abstaining, the remaining three signals
renormalise against 0.50 — so their *effective* weights become semantic
40%, location 30%, title 30%. Location and title are close to binary in
this data. A city match is worth a flat 0.30, while the entire semantic
range from 0.51 to 0.96 is worth about 0.18.

The observable effect: the job with the **highest semantic similarity
in the whole corpus** (0.964, "AI/ML MLOps Engineer") ranked **fourth**,
below jobs at 0.657 and 0.578 that happened to be in a preferred city.

The arithmetic is correct — it was checked by hand. But it means that
while the model is half-covered, the ranking is mostly answering "is
this the right city?" rather than "is this the right job?". This should
resolve on its own once enrichment runs: with skill active,
`weight_covered` rises to 0.80 and location's effective weight falls
from 30% to 19%.

**A related effect worth watching.** Because an abstaining signal is
removed from the denominator, a job with *missing* location data can
outrank a job with *bad* location data. A measured 0.0 stays in the
denominator; an abstain does not. Observed directly: a job at semantic
0.726 with an abstained location and an agency penalty outranked a job
at semantic 0.730 with a measured location of 0.0 and no penalty at
all.

This is not a bug — it is what abstention means, applied consistently.
But it does mean abstention is mildly *rewarded* rather than neutral,
and that was not a decision anyone made. It is recorded here as a known
property, not a resolved one.

---

## Part 7. What Day 9 can assume

- **Scoring runs end to end against real data.** `scoring_runs` has
  rows; `recommendations` is populated with a full ranking.
- **`run_scoring(user_id=None)` scores every user with a profile**;
  `user_id=N` scores one. `dry_run=True` computes everything and writes
  nothing.
- **The funnel is asserted three ways at run time**, including
  `pairs_scored == jobs_scored × users_scored`, which is the only one
  that checks the loop rather than the plan.
- **Every signal column on `recommendations` is nullable and NULL means
  abstain.** A test forces them to stay that way.
- **`weight_covered` is stored on every row.** Two rows with the same
  `final_score` and different coverage are not the same claim.
- **`inputs_fingerprint` is a SHA-256 over the profile timestamp,
  sorted profile skills, both job source hashes, and
  `weights_version`.** It catches *our* inputs and *our* rules
  changing. It does **not** catch a job posting changing at the source,
  because stored job text never changes after insert.
- **`select_status()` and `is_notify_eligible()` are pure functions**
  and can be called without a database.
- **Ties in `rank()` break by `job_id` ascending**, stated explicitly
  rather than left to sort stability.
- **All three notification gates use `>=`.** The boundary value
  qualifies, and each is tested at exactly its floor.

### What Day 9 must NOT assume

- **That the skill signal is active.** It abstains on 96 of 98 pairs.
- **That the experience signal is active at all.** It abstains on 98 of
  98. It has never contributed to a single score.
- **That `notify_eligible > 0` is achievable today.** With
  `weight_covered` at 0.50 and the minimum at 0.55, no pair can
  currently clear the gate regardless of fit. This is the gate working,
  not a bug — but a Day 9 notification pipeline built on top of it will
  correctly produce nothing until enrichment runs.
- **That `COMPLETE_NO_QUALIFYING` means the system is healthy.** It
  currently means exactly that *and* that half the model is dark. Read
  the abstain counters alongside it, always.
- **That `PARTIAL` or `FAILED` will ever appear** in `scoring_runs`.
  Nothing sets them.
- **That a job's `work_mode` is known.** It is NULL on 94 jobs, so
  `jobs_remote` and `jobs_hybrid` are both 0 and the remote branch of
  `score_location()` has never executed against real data.

---

## Part 8. Open issues carried into Day 9

### 8.1 Enrichment coverage is the bottleneck

94 of 99 jobs have never been enriched. The pipeline is not broken —
`list_needing_enrichment()` already selects them correctly and the
script already has the flags it needs. It has simply never been able to
run, because the Gemini free tier is **daily** and grants roughly one
call before returning 429.

A dry run estimates 97 calls at 27–84 minutes.

### 8.2 Experience may not be recoverable from this data

Even the 5 enriched jobs returned `null` for both experience bounds.
`ENRICHMENT_PROMPT` asks for them correctly and explicitly distinguishes
`null` (silent) from `0` (welcomes freshers), so the prompt is not at
fault.

A regex over stored descriptions found that only **37 of 99** mention
years at all, and average description length is **495 characters** —
against Adzuna's hard cap of 500. Almost every description is
truncated.

So the ceiling is upstream, at ingestion, not at enrichment. Even a
complete enrichment pass should be expected to leave `abstain_experience`
somewhere around 60, not 0. **That is a finding about the source data,
not a failure to fix** — and writing the prediction down before the run
is what will make the result readable.

### 8.3 Job 81 is permanently locked out

Job 81 has no skills and does not appear in the enrichment candidate
list, which means its `skills_extraction_attempts` has reached 3. It
will never be enriched by a normal run again, only by `--retry-failed`.
Three rows carry a `skills_extraction_error`, all predating the quota
fix. This is a row that has silently left the pipeline.

### 8.4 An excluded job consumes the first call of every run

`list_needing_enrichment()` does not filter `is_excluded` —
deliberately, since having skills and being scorable are different
questions. But the consequence is that job 2, the known junk posting,
is first by id and takes the first API call of every run. On a day that
grants one call, that is the entire budget spent on a row whose answer
is already known and whose skills would never be used.

### 8.5 Two counts that look like they should agree

`enrich_jobs --dry-run` prints `missing skills 91` and
`would enrich 97`. The first counts rows with no attempt recorded; the
second counts rows with no skills and attempts below the ceiling. Both
are correct and they measure different things, but printed adjacently
they invite a subtraction that means nothing.

### 8.6 Smaller things

- **`scripts/pack.ps1` still does not exist.** Nine credential leaks so
  far, every one from a hand-made zip rather than `git archive`. The
  archive shared during this session was hand-made and included
  `.git/`, `storage/` and a stray nested zip — all three excluded by
  `.gitignore`. No credentials were in it (`.env` has never been
  committed, verified against git history), but 20 real CV PDFs were.
  The tenth incident is a matter of time.
- **The `--top` table prints the `title` header twice.** Cosmetic.
- **The agency list may be incomplete.** Six more candidates were found
  in the full company list, which would take the affected pair count
  from 29 to 35. Undecided.
- **`python -c` sometimes prints nothing in this shell**, with no error.
  Unexplained. Use a file. This bit again during this session, in a
  different form: a JSON literal passed through PowerShell quoting
  arrived at Postgres with its quotes stripped and its backslashes
  intact. The fix was the same — write a file.

---

## Part 9. Where things stand

| | |
|---|---|
| Alembic head | `9a4e7c1d5b82` — 8 migrations |
| Tests | **363 passing** |
| Jobs | 99, all embedded, 1 excluded |
| CV versions | 3 active, all embedded |
| Enriched jobs | 5 — of which 2 produced skills |
| Jobs with experience bounds | **0** |
| `scoring_runs` rows | 2 |
| `recommendations` rows | 98 |
| Active signals | 3 of 5 |

---

## Part 10. Lessons worth carrying forward

**A funnel that balances may be checking the plan, not the work.** Both
original assertions balanced perfectly while a real job was dropped
from every run. The number that moved was `pairs_scored`, and nothing
was comparing it to anything. When you add a check, ask which stage it
actually observes.

**A success status is not success.** The first real run reported
`complete_no_qualifying` — a status explicitly documented as healthy —
while 40% of the model had never contributed a single value. When
something adds a status, a count or a score, ask what it would look
like if the work silently did nothing.

**When a failure has several plausible causes, build the thing that
separates them.** `abstain_experience = 98` had two explanations:
missing candidate data or missing job data. One query settled it in ten
seconds. Guessing would have cost a day.

**Write the prediction down before the run.** Before widening the
user's preferences, three predictions were recorded: `abstain_location`
stays at 23, title starts returning fractional values,
`notify_eligible` stays 0. All three held — which is what made the
*fourth* observation, the ranking inverting on semantic similarity,
legible as a surprise rather than as noise.

**A cheap check before an expensive one.** Enriching 94 jobs costs a
full day's quota. A regex over stored descriptions costs nothing and
predicts the outcome. The order matters more than either check.

**Two explanations for one observation means the checking is not
finished.** This applies to documents as well as data: the working
notes' claim about where the weights came from did not survive contact
with the document itself, and recording that gap is worth more than a
tidy sentence would have been.

**Invisible is worse than wrong.** Every fix in Part 4 has the same
shape: something was happening that produced no number anyone could
read. The fix was never only to correct the behaviour — it was to make
the behaviour countable.