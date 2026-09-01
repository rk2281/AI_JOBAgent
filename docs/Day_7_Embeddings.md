# Day 7 — Embeddings and Vector Search

A record of what Day 7 built, why each choice was made, what went
wrong, and what Day 8 can rely on.

---

## Part 1. What Day 7 was for

Day 6 left 99 job postings in the database. Every one had a title, a
company, a location, a URL and a description. Every one also had an
`embedding` column that was `NULL`.

Day 7's job was to fill that column, do the same for the CV side, and
make it possible to ask: _given this person's CV, which of these 99
jobs are the closest match?_

That question is the foundation Day 8 scores on. Day 8 gives semantic
similarity 20% of a job's total score. Day 7 is where that 20% comes
from.

---

## Part 2. The concepts, from scratch

### 2.1 What an embedding is

An embedding is a list of numbers that represents a piece of text.

Feed "Machine Learning Engineer" to an embedding model and you get back
768 numbers. Feed "ML Engineer" and you get 768 different numbers — but
they will be _close_ to the first set. Feed "Restaurant Floor Manager"
and you get 768 numbers that are far away from both.

That is the whole idea. Meaning becomes distance. Two texts that mean
similar things produce number-lists that sit near each other.

Nobody decides what each of the 768 numbers means. They come out of a
model trained on enormous amounts of text, and no individual number has
a name. What matters is only the relationship between whole lists.

### 2.2 What a vector is, and what a dimension is

The list of 768 numbers is called a **vector**. The count — 768 — is
its **dimension**.

It helps to think in 2 dimensions first. A vector of 2 numbers, say
`[3, 4]`, is a point on a flat map: 3 across, 4 up. You can measure how
far apart two such points are. Embeddings do the same thing with 768
numbers instead of 2. You cannot picture 768 axes, but the arithmetic
works identically.

Two vectors can only be compared if they have the same dimension. A
768-number vector and a 3072-number vector are not comparable at all —
there is no meaningful distance between them.

### 2.3 Cosine distance, and why length does not matter

There are several ways to measure how far apart two vectors are. The
one this project uses is **cosine distance**.

Cosine ignores how long each vector is and looks only at the direction
it points. `[1, 2]` and `[10, 20]` point in exactly the same direction,
so their cosine distance is 0 — they are treated as identical in
meaning, even though one is ten times longer.

This is the right choice for text. A long document and a short one
about the same subject should count as similar, and cosine gives that
for free.

In PostgreSQL, with the pgvector extension installed, cosine distance
is written with the `<=>` operator:

```sql
SELECT title, embedding <=> :query_vector AS distance
FROM jobs
ORDER BY embedding <=> :query_vector
LIMIT 10
```

Distance 0 means identical direction. Distance 1 means unrelated.
Similarity is simply `1 - distance`.

### 2.4 What a unit vector is, and why we made ours unit vectors

A vector's **length** (also called its norm) is how far the point sits
from the origin. For `[3, 4]` the length is 5, by Pythagoras:
`sqrt(3² + 4²) = 5`.

A **unit vector** is one whose length is exactly 1. Any vector can be
turned into a unit vector by dividing every number in it by its length.
`[3, 4]` becomes `[0.6, 0.8]`, which has length 1 and points in exactly
the same direction.

Cosine distance does not care whether vectors are unit length — it
divides length out anyway. So normalising buys nothing for search.

We do it regardless, for a different reason. If every stored vector has
length exactly 1.0, then a single query can check that they all still
do, and any row that has been corrupted stands out. If lengths are
allowed to vary — 0.58 here, 0.61 there — there is no way to tell a
damaged vector from a healthy one. **Normalising turns an invisible
kind of corruption into a checkable one.**

### 2.5 What an HNSW index is, and its hard limits

Finding the closest vector to a query means comparing the query against
every stored vector. At 99 rows that is instant. At a million rows it
is not.

**HNSW** (Hierarchical Navigable Small World) is an index that makes
this fast. Instead of comparing against everything, it builds a graph
where each vector is connected to its neighbours, and walks the graph
towards the answer. It gives up perfect accuracy for enormous speed:
it usually finds the true nearest neighbours, not always.

Two things about HNSW mattered on Day 7:

**It has a hard dimension limit of 2000.** A vector column can hold
more than 2000 dimensions, but no HNSW index can be built on it. This
single fact decided the biggest question of the day (see Part 3.2).

**It must match the operator you query with.** The index was created
with `vector_cosine_ops`, which pairs with `<=>`. If a query used `<->`
(a different distance) instead, the index would simply be ignored. No
error, no warning — just a slower query returning different rows.

### 2.6 Task types: document versus query

Embedding models can be told what a piece of text is _for_.

Embedding a job posting that will sit in a database waiting to be
found is a `RETRIEVAL_DOCUMENT`. Embedding a CV that is doing the
searching is a `RETRIEVAL_QUERY`. The model places them slightly
differently.

This exists because a CV and a job advert are written in completely
different registers. A CV is a first-person career history. A job
advert is a third-person pitch. If both were embedded identically, a
large part of the measured similarity would be capturing _"these are
two different kinds of writing"_ rather than _"this person fits this
job"_. The task-type split reduces that.

Crucially, getting this wrong produces no error. It just produces
slightly worse matches, forever.

### 2.7 Matryoshka embeddings, and truncation

Some embedding models are trained so that the _first_ part of the
vector carries most of the meaning. Cut a 3072-number vector down to
its first 768 numbers and you still have something useful. This is
called **Matryoshka** representation, after the nesting dolls.

That is how a model whose natural output is 3072 numbers can be asked
for 768 instead. It does not re-run anything; it hands back a prefix.

One consequence matters: the full 3072-vector is unit length, but the
768-number prefix is not. Cutting a vector shortens it. Our measured
value was **0.5856**, and that is a _healthy_ number — if the meaning
were spread evenly across all 3072 positions, a quarter of them would
give a length near 0.5. Getting 0.586 shows the front of the vector is
carrying more than its share, which is exactly what Matryoshka training
is meant to achieve.

---

## Part 3. The decisions

Every decision below was made before code was written, and most were
settled by measurement rather than by reading documentation.

### 3.1 First decision: measure before choosing

Day 5 lost three hours to a cycle of change-something-and-retry. The
lesson was to build the thing that separates causes instead of guessing
the next one. `scripts/gemini_isolate.py` and `scripts/source_isolate.py`
both exist because of that.

Day 7 started the same way, with `scripts/embedding_isolate.py`. It
makes six escalating calls and answers six questions in one run:

| Stage | Question                                                                                |
| ----- | --------------------------------------------------------------------------------------- |
| A     | What does the installed SDK's embedding interface actually look like? (no network call) |
| B     | Which models does this API key list as supporting embedding?                            |
| C     | What dimension does the model return by default, and is it unit length?                 |
| D     | Is `output_dimensionality=768` accepted, and is the result unit length?                 |
| E     | Is `task_type` applied, or silently ignored?                                            |
| F     | Does a batch return one vector per input, in the right order?                           |

Stage E deserves attention. It is the only one with no error path. Both
calls succeed either way; the answer is in a number. It embeds the same
text twice, once as a document and once as a query, and compares them.
A cosine of exactly `1.000000` means the two vectors are byte-identical
and the parameter was thrown away.

Without this script, every decision below would have been a guess, and
a wrong guess about dimension costs a migration, an index rebuild and a
re-embed of every row.

### 3.2 Which model, and which dimension

The `embedding` column and both HNSW indexes were created on Day 2 as
`vector(768)`. The question was whether to keep that.

It turned out not to be a real choice. The model's natural output is
**3072** dimensions, and HNSW cannot index anything above 2000. Going
native would mean keeping the vectors but losing the index entirely.
768 was already correct.

The API key listed three models. Two were tested:

|                   | `gemini-embedding-001`        | `gemini-embedding-2`          |
| ----------------- | ----------------------------- | ----------------------------- |
| default dimension | 3072, unit length             | 3072, unit length             |
| at 768 dimensions | works, length **0.5856**      | works, length 1.0             |
| `task_type`       | **applied** (cosine 0.861247) | **ignored** (cosine 1.000000) |
| batch of 8 inputs | **8 vectors, correct order**  | **1 vector**                  |

`gemini-embedding-2` looked better on the one measure that does not
matter. It returns a unit-length vector at 768 dimensions — but we
normalise anyway, so that is worth nothing.

It failed on both measures that do matter. It ignores `task_type`,
which is the thing that makes a CV comparable to a job advert. And it
returned **one vector for eight inputs** — not an error, one vector.
Code that trusted that response would have attached a single embedding
to the first job and silently lost the other seven.

**Decision: `gemini-embedding-001`, at 768 dimensions, normalised
client-side.**

A note on reading two anomalies as one cause. The first instinct was
that `gemini-embedding-2` must be ignoring the whole config object,
which would explain both failures at once. Stage D disproves it: asking
for 768 dimensions _worked_, so the config was read. They are two
separate faults. The tidy explanation was wrong.

### 3.3 What text gets embedded

Day 6 recommended building the job document from
`title + company + location + description`. Day 7 rejected two of those
four.

**`title` — kept.** The highest information per token in the entire
row. "Senior Machine Learning Engineer" says more about fit than the
500-character blurb does.

**`description` — kept, with known limits.** Adzuna caps descriptions
at exactly 500 characters, measured identically across 27 real
postings. That is roughly 100–125 tokens: enough to establish the
domain, not enough to contain a requirements list.

**`location` — removed.** Three reasons. Day 8 already scores location
deterministically at 15% with exact rules, so putting it in the vector
counts one fact twice, the second time badly. The same role in two
cities would get different vectors, which is wrong — the vector should
capture what the job _is_. And 23 of the 99 rows have location `"India"`
with no city, so the field is inconsistently present, contributing
noise where it contributes anything.

**`company` — removed.** Three staffing agencies account for 23 of the
99 rows. Embedding the employer name makes those 23 cluster by _who
posted them_ rather than by what the work is; the vector learns
"agency-ness". A company name says almost nothing about the job itself.
The counter-argument — a user who wants product companies rather than
agencies — describes a deterministic preference filter, not a semantic
signal.

Final format, labelled rather than concatenated:

```
Job title: Senior Machine Learning Engineer

Description: Build and deploy ML models in production...
```

The labels cost a few tokens and give the model an explicit boundary
between the fields. That matters more than usual here, because a
500-character excerpt gives the model little structure to infer.

For the CV side, `location`, `total_experience_years` and `education`
are all left out — the first two for the same double-counting reason
(Day 8's 15% and 20% signals), and education because it mostly
contributes institution names, which cluster the way company names do.

Skills **are** included, which looks inconsistent since skills are Day
8's 30% signal. The reason is symmetry. A job's description names
technologies whether we like it or not. Removing skills from the CV
side would leave one side listing them and the other not, and the
similarity would then partly measure "these are different kinds of
document" — the exact problem the task-type split exists to reduce.

CV skills come from `cv_versions.extracted_profile`, which holds the
CV's own spellings, **not** from `profiles.skills`, which holds
normalized catalog keys like `cpp` and `ansys fluent`. Catalog keys are
a matching surface for exact set comparison; they are worse input to a
language model than the words the CV actually used.

### 3.4 Where the code lives

| Concern                          | Location                                                        | Why                                                                                                                                                  |
| -------------------------------- | --------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| Talking to Gemini                | `app/integrations/gemini_embeddings.py`                         | The only layer allowed to import a third-party SDK. A new file rather than an addition to `gemini.py`, which is the CV-extraction client and says so |
| What text represents a job or CV | `app/services/embedding_text.py`                                | This is a _rule_, not a client detail. Day 8 will reuse `build_job_document()` as the input to job-skill extraction                                  |
| Running a pass                   | `app/services/job_embedding.py`, `app/services/cv_embedding.py` | Which rows, batching, status, transactions                                                                                                           |
| SQL                              | `app/db/repositories/job.py`, `cv.py`                           | The only place SQL is written — including the `EXPLAIN` statements                                                                                   |
| Running it by hand               | `scripts/embed_jobs.py`, `embed_cvs.py`, `search_jobs.py`       | Argument parsing and printing only                                                                                                                   |

### 3.5 Batch or one at a time

`gemini-embedding-001` accepts a list and returns one vector per input
in order, verified by embedding `[X, Y, X]` and confirming vectors 0 and
2 are identical. So jobs are embedded eight at a time: 13 calls instead
of 99.

But a batch is all-or-nothing, and the response does not say which
input offended. So when a batch fails, the service **falls back to one
call per row** to isolate the bad one. Nineteen good rows are not lost
to one bad one. This is the same thing `embedding_isolate.py` does at
debugging time, done here at run time.

CVs are embedded one at a time, because `embed_query` takes a single
text and there are only three rows.

### 3.6 When embedding runs

**As a separate pass, not inside ingestion.** Day 6 deliberately made
`embedding` nullable so ingestion would never block on an API call, and
Day 7 keeps that separation.

Three reasons, and the first is the one that matters:

The two are rationed by **different quotas**. Adzuna allows a fixed
number of calls per month, and those calls are already spent by the
time embedding would begin. Letting a Gemini failure abort a run whose
Adzuna budget is gone trades a cheap, repeatable failure for an
expensive, unrepeatable one.

The embedding pass is **idempotent and re-runnable**. Ingestion is not.
Coupling them binds the safe thing to the unsafe one.

Changing the model, or changing `build_job_document()`, requires
**re-embedding without re-ingesting**. If embedding only happened inside
ingestion, that would be impossible.

There is a config flag, `EMBED_AFTER_INGESTION`, default off, for Day 10
to turn on once scheduling exists.

### 3.7 What happens when a row fails

This was the most important decision of the day, because of one fact:

> A row with a `NULL` embedding is not ranked low. It is **absent**.
> `ORDER BY embedding <=> :q` never returns a NULL row. Nothing raises,
> nothing logs, and the job simply stops existing as far as matching is
> concerned.

So `NULL` alone is not enough information. Five columns were added to
**both** `jobs` and `cv_versions`:

| Column                  | What it distinguishes                                    |
| ----------------------- | -------------------------------------------------------- |
| `embedding_model`       | Which model produced this vector                         |
| `embedded_at`           | When                                                     |
| `embedding_attempts`    | `0` + NULL = never tried. `>0` + NULL = tried and failed |
| `embedding_error`       | Why, as a safe string                                    |
| `embedding_source_hash` | What text it was built from                              |

`embedding_model` is not optional bookkeeping. Both models on this key
return 768 dimensions, so rows from the two are **identical in the
column** and meaningless as neighbours of each other. This is the only
thing that would make such a mix detectable.

`embedding_attempts` is what stops a permanently broken row from being
retried on every run forever, spending quota to fail identically.

`embedding_error` is written only from `describe_genai_error()`, which
reads an exception's `code` and `message` attributes and never its
string form. This carries forward Day 6's rule: an error object is not
safe to format just because it is an error, because its string form can
carry the request that produced it — and on this path that request is
job text or a candidate's CV.

Two guards run before anything is stored. A vector of the wrong length
is rejected. A vector of length exactly zero is rejected, because a zero
vector is equidistant from everything: its row would land at an
arbitrary point in every ranking while the column looked perfectly
populated.

### 3.8 What "the same job changed" means

The obvious reading of `embedding_source_hash` is that it catches the
source rewriting a posting. Reading the Day 6 code showed that is wrong.

`compute_content_hash()` covers title, company and location — **not the
description**. And `mark_seen()` updates only `last_seen_at` and
`is_active`; it refreshes no text at all. So a stored job's embedding
input **can never change after insert**. A changed title produces a
different content hash and becomes a new row instead.

What the hash actually protects against is **our own rule changing**.
The day `build_job_document()` gains a field or alters its format, every
stored vector silently becomes stale while continuing to look valid.
`--recheck` compares each row's stored hash against what the builder
produces today and re-embeds the mismatches.

The column is still worth having. Its stated reason is just different
from the assumed one.

### 3.9 Distinguishing the kinds of "nothing"

Day 4 wrote `status=complete` for extractions that produced nothing.
Day 5 added a state for "succeeded and said nothing". Day 6 built a
six-status funnel because "0 jobs added" had six causes and only one was
healthy.

Day 7 has the same shape, so `embedding_runs` records a funnel with
eight statuses:

| Status           | Meaning                                             |
| ---------------- | --------------------------------------------------- |
| `running`        | Written before any work, so a crash leaves evidence |
| `complete`       | Everything attempted succeeded                      |
| `partial`        | Some succeeded, some failed                         |
| `nothing_to_do`  | Rows exist, all already current — **healthy**       |
| `no_source_rows` | No eligible rows at all — **broken**                |
| `all_failed`     | Attempted, nothing survived                         |
| `provider_error` | Could not reach the provider                        |
| `quota_exceeded` | Quota spent                                         |

`nothing_to_do` and `no_source_rows` are the pair worth keeping apart.
Both mean zero rows embedded. They mean opposite things: work finished,
versus nothing to work on. On a table that should hold 99 active jobs,
the second means ingestion or the eligibility filter is broken. A single
"0 rows" log line would merge them.

The counters are a funnel, not a summary, and the arithmetic is
asserted:

```
candidates_considered == skipped_empty_text + attempted + abandoned
attempted             == succeeded + failed
```

Separately, `remaining_null` is measured **after** the pass by counting
rows that still have no vector. It is not derived from the funnel. A
run can report `succeeded=40` and still leave 59 rows invisible to every
query; the funnel alone would call that a success.

---

## Part 4. What went wrong

### 4.1 The credentials leaked again — the seventh time

The project zip uploaded at the start of Day 7 contained `.env`, the
entire `.git` directory, and 13 real CV PDFs belonging to users.

Day 6 had already established the fix: `git archive --format=zip`, which
can only pack files Git tracks. That command was not the one that ran.
The archive contained `.git/`, which `git archive` can never produce, so
it was a plain folder zip.

All five credentials were rotated: Telegram bot token, Gemini API key,
Neon password, and both Adzuna keys. The count had grown from three to
five since Day 6 and the old checklist was out of date.

**The lesson is not "use the safe command".** That was already known.
The lesson is that a safe command nobody verified ran is the same as no
safe command. The check now runs after building the archive and before
sending it:

```powershell
$dst = Join-Path $env:TEMP "ziptest"
Remove-Item $dst -Recurse -Force -ErrorAction SilentlyContinue
git archive --format=zip -o ..\AI_JOB_HUNT_AGENT.zip HEAD
Expand-Archive -Path ..\AI_JOB_HUNT_AGENT.zip -DestinationPath $dst -Force
$hits = Get-ChildItem -Path $dst -Recurse -Force -Filter ".env" -ErrorAction SilentlyContinue
if ($hits) { "LEAK: .env is in the archive" } else { "clean" }
Remove-Item $dst -Recurse -Force
```

`git archive` also excludes `storage/cvs/`, which holds other people's
personal data and had no business being in an archive either.

### 4.2 The quota is per minute, not per day

The first full run failed partway through with:

```
Quota exceeded for
global_embed_content_requests_per_minute_per_base_model
```

Nine calls succeeded and the tenth failed, so the ceiling is around ten
requests per minute. Thirteen batches fired back to back will always hit
it.

The fix is pacing: `EMBEDDING_SECONDS_BETWEEN_CALLS`, default **7.0**.

Not 6.0. Ten per minute means 6.0 seconds apart is _exactly_ the limit,
and a threshold hit exactly at its boundary is the case that fails while
looking like it should pass. This is Day 6's lesson — a check written
`median < 500` stayed silent when the real median was exactly 500 — and
it applies to rate limits as much as to comparisons.

### 4.3 The funnel assertion fired, and the assertion was right

The first two runs both logged:

```
Embedding funnel does not balance:
{'candidates_considered': 8, 'attempted': 0, ...}
```

The check was not wrong. The model behind it was.

It assumed every candidate ends up either skipped or attempted. When a
quota abort breaks out of the loop, the remaining candidates are
neither. There was **no name for where they went**.

The fix was to name the gap, not to loosen the check: `abandoned`, a
derived value equal to `candidates − skipped − attempted`. It needs no
column, because it is exactly what the other three leave over — storing
it would create a second copy of a number that can disagree with the
first.

`accounted_for()` deliberately allows `abandoned > 0`. An aborted run
legitimately leaves rows untouched, and treating that as corruption
would cry wolf on the one path that already reports itself honestly.
What must never happen is a **negative** `abandoned` — more rows
processed than selected — or an `attempted` count that does not split
into successes and failures.

A second, smaller bug came out of the same log. `attempted` was being
counted _after_ the API call returned, so a call that raised recorded
`api_calls: 1` with `attempted: 0` — eight rows were sent and none
counted. It is now counted at send time, and decremented on a quota
error, because a 429 means the request was **refused**, not answered.
Those rows were never seen by the provider, so they belong in
`abandoned`.

**This is the best thing that happened on Day 7.** The check existed to
shout if rows went missing. It shouted — at the model that wrote it.
Without it, runs 1 and 2 would have reported success.

### 4.4 What worked: abort and resume

Run 2 embedded 72 rows and hit the quota. Run 3 picked up exactly the 27
that were left and finished.

That worked because each batch commits in its own transaction. Had the
whole pass been one transaction, the 429 would have rolled back 72 rows
and wasted nine successful API calls.

---

## Part 5. How it was verified

### 5.1 The migration

Migration `f2c81b3ea774`, written by hand. `--autogenerate` is never
used in this project, because it cannot see the two HNSW indexes — they
were created with raw `op.execute()` and are absent from
`Base.metadata`. It has twice proposed dropping them.

Cycled `upgrade → downgrade → upgrade`, with
`python -m scripts.check_indexes` after each step. All three reported
both indexes present.

The existing 99 rows picked up `embedding_attempts = 0` from the column's
server default, confirmed by query — not NULL, which would have broken
the `attempts > 0` logic from day one.

### 5.2 The embedding itself

Against the real API and the real rows, not mocks:

- **99 of 99 jobs embedded**, `remaining_null: 0`
- **3 of 3 active CV versions embedded**, `remaining_null: 0`
- Every stored vector has length **exactly 1.000000**
- Every row records `embedding_model = 'gemini-embedding-001'`
- Re-running produces `nothing_to_do`, `candidates 0`, `API calls 0` —
  it is idempotent
- `truncated: 0` on both sides, so no document was silently cut

The CV scope is worth noting. Only **3** versions were embedded, not
every version of every CV. User 2 alone has 24 `cvs` rows, most of them
failed experiments from the Day 5 Gemini investigation. The scope is
`profiles.active_cv_version_id` — the version a profile was actually
built from, which is guaranteed never to be one that failed the
emptiness check.

### 5.3 The search

**Self-check.** Searching with a job's own stored vector returns that
job first, at similarity `1.0`. This costs nothing and proves the whole
round trip: the vector that left the provider is the vector that comes
back out of Postgres, and the ordering works. It separates "is storage
correct" from "are the embeddings any good" — two questions that would
otherwise be debugged together.

**The index.** At 99 rows the planner chooses a sequential scan, and
that is **correct** — reading 99 rows costs less than consulting an
index. Proving the index works therefore needs two plans:

```
default:                Seq Scan on jobs            0.402 ms
enable_seqscan = off:   Index Scan using
                        ix_jobs_embedding_hnsw     25.642 ms
```

The second confirms the operator, the opclass and the query shape all
agree, so the index will be used once the table is large enough to earn
it. The index being 60× _slower_ here is the expected result and is why
the planner is right to ignore it.

Anyone who tried to make `EXPLAIN` say "Index Scan" at this table size
by changing the query would only produce a worse query.

---

## Part 6. The result, and the problem it revealed

Matching user 2's CV — an AI/ML CV — against all 99 jobs:

```
0.6928   AI/ML MLOps Engineer
0.6917   Applied AI Solutions Architect
0.6670   Senior Artificial Intelligence & Machine Learning Engineer
0.6460   Frontend Engineer
0.6453   Engineering Manager
...
0.5309   Oracle EPM FCCS AM Consultant
0.5279   Producer (Brut Tamil)
0.5058   Sales Promoter
```

**The direction is right.** The top three are exactly the three AI/ML
jobs. The bottom is sales, media and catering. And the gap between rank
3 and rank 4 is **0.021**, against a mean adjacent gap of **0.001** for
ranks 4–90. The top three genuinely separate; that is not noise.

**But the range is badly compressed.**

|                            |              |
| -------------------------- | ------------ |
| highest                    | 0.6928       |
| lowest                     | 0.5058       |
| total spread               | **0.187**    |
| standard deviation         | 0.032        |
| jobs between 0.59 and 0.65 | **49 of 98** |

Half the jobs sit in a band 0.06 wide. Frontend Engineer (0.6460) ranks
_above_ Lead Data Engineer (0.6301) and AWS Data Engineer (0.6294) for
an ML CV, which is wrong. That is the 500-character description showing
its limits.

This is not a bug. Two English documents in the same broad domain have
a high similarity floor no matter how different they are. The absolute
numbers carry little meaning; only the ranks do.

**What it means for Day 8:**

```
semantic_score = raw similarity, at 20% weight:
  best match    13.86 points
  worst match   10.12 points
  spread         3.74 points, out of 20 available
```

A 20% weight would behave like roughly a 3.7% weight. Every job gets
the other 16 points regardless. The signal is crushed.

This is exactly the failure Day 6 warned about: a weak embedding does
not look broken, it surfaces as mediocre matches that look like a
scoring problem. The difference is that it is now visible **before** Day
8 rather than during it.

---

## Part 7. What Day 8 can assume

- **All 99 active jobs have an embedding.** `remaining_null` is 0 and
  the pass is idempotent.
- **All 3 active CV versions have an embedding**, scoped to
  `profiles.active_cv_version_id`.
- **Every vector is 768 dimensions, unit length, from
  `gemini-embedding-001`.** Jobs are `RETRIEVAL_DOCUMENT`, CVs are
  `RETRIEVAL_QUERY`.
- **Matching a CV against every job costs no API call.** Both sides are
  already vectors. Only free text typed by a user needs the provider.
- **`search_for_user(user_id)` returns `list[JobMatch]`**, nearest
  first, each with `similarity` in `[0, 1]`. It returns **`None`**, not
  an empty list, when the user has no embedded CV — "could not search"
  and "searched and found nothing" are different and must not be shown
  the same way.
- **`JobMatch` is frozen.** A score that can be edited in transit is a
  score nobody can trace.
- **Both HNSW indexes exist and are usable.** The planner will start
  choosing them as the table grows.
- **`build_job_document(title, description)` is the canonical text for a
  job.** Day 8's job-skill extraction should use the same function, so
  the embedding and the skill extractor look at the same job.
- **`embedding_runs` records both scopes**, `jobs` and `cv_versions`, in
  one table with an eight-status funnel.

### What Day 8 must NOT assume

- **That raw similarity is usable as a 0–1 score.** See Part 6. It needs
  rescaling within the candidate set, or the 20% weight will behave like
  4%.
- **That `job_skills` is populated.** It is still empty. See Part 8.
- **That a high rank means a good match.** Rank and quality are
  different questions.

---

## Part 8. Open issues carried into Day 8

### 8.1 Job-skill extraction is still unowned — and it is Day 8's largest signal

The scoring table gives skill match **30%**, the biggest single
component. Nothing populates `job_skills`. Day 6 said normalize and
validate; Day 7 did embeddings; Day 8 assumes the skills are already
there. They are not.

This belongs at the **start of Day 8**, not in Day 7. Day 7's output does
not depend on it, and it is a scoring surface — it must align with
`skills.normalized_name`, the same catalog `profiles.skills` uses, or
the 30% signal scores zero for every pair.

**One thing Day 8 must plan for now.** Between 500-character
descriptions, a tech-heavy skills catalog and domain-neutral ingestion,
the extractor will find **zero skills** for many jobs. If Day 8 treats
that as `0/30`, every non-tech job ranks permanently below every tech
job for a reason that is a _data gap_, not a _fit gap_.

"No extractable skills" must be an **abstain**, not a zero —
renormalise the remaining 70% for that job.

### 8.2 The score compression problem needs two numbers, not one

Day 8 should not use raw similarity directly. But normalising within
the candidate set has its own trap: **the best match always scores 1.0,
even when it is terrible.** On a day when only catering jobs were
ingested, a fresher's top match would receive a full semantic score.

So Day 8 needs two separate numbers:

- **For ranking** — similarity rescaled within the candidate set, so the
  20% weight behaves like 20%
- **For notifying** — a floor on the _raw_ similarity, e.g. do not send
  a message below about 0.62. Only 12 of 98 jobs clear that, and they
  include all three genuine ML matches

Using one where the other belongs is the same mistake Day 4 made with
`status=complete`. Rank answers "which of these is best". Quality
answers "is any of these worth sending". They look alike and are not.

### 8.3 Smaller things

- **`truncated` is counted but not stored.** It is printed and logged.
  It has been 0 on both sides so far. If the CV side ever shows non-zero,
  it earns a column.
- **Data quality is still unhandled.** One description is a recruiter
  advertising his own LinkedIn. Three staffing agencies account for 23
  of the 99 jobs. 23 jobs have `location = "India"` with no city. Day 6
  deliberately left these for Day 8 as scoring penalties, not filters.
- **`is_active = False` still means "unseen for 21 days"**, a guess
  about closure rather than a fact. The embedding pass skips inactive
  rows to avoid spending quota on rows no query will ask for. If that
  guess turns out to be wrong, the fix is to re-run the pass.
- **The `provider_error` path is unproven end to end.** `quota_exceeded`
  has now been seen for real, twice. `provider_error` has not.

---

## Part 9. Where things stand

|                         |                                                                           |
| ----------------------- | ------------------------------------------------------------------------- |
| Tests                   | 145 at the start of Day 7, 259 at the end                                 |
| Migrations              | 7, head `f2c81b3ea774`                                                    |
| Jobs                    | 99, all embedded                                                          |
| CV versions             | 3 active, all embedded                                                    |
| Embedding runs recorded | 5                                                                         |
| Model                   | `gemini-embedding-001`, 768 dimensions, unit length                       |
| New scripts             | `embedding_isolate.py`, `embed_jobs.py`, `embed_cvs.py`, `search_jobs.py` |

---

## Part 10. Lessons worth carrying forward

**Measure the provider before designing around it.** Every important
Day 7 decision came from `embedding_isolate.py`, and two of them
contradicted the reasonable assumption. The newer model was worse. The
truncated vector was not unit length.

**The check that fires against your own model is the check working.**
The funnel assertion did not find a bug in the data. It found a gap in
the thinking that wrote it. That is more valuable, and it only happened
because the arithmetic was asserted rather than assumed.

**A tidy explanation for two anomalies is a warning sign.** "The SDK is
dropping the config" would have explained both of `gemini-embedding-2`'s
failures at once. Stage D disproved it in one line. The same pattern as
Day 6's `posted_at` clustering: the first explanation that fits is not
the same as the right one.

**Thresholds fail at their boundary.** 6.0 seconds against a 10-per-
minute limit is exactly the limit. 7.0 is under it. Day 6's
`median < 500` against a real median of exactly 500 was the same
mistake in a different costume.

**Invisible is worse than wrong.** A row with no vector is not ranked
low — it is gone, silently. Nearly every design choice in Part 3.7 and
3.9 exists to convert that silence into a number someone can read.

**Verify the safe command ran.** The credential leak did not happen
because the right procedure was unknown. It happened because nobody
checked that it was the procedure that executed.
