# Day 6 — Job Ingestion & Validation

_What we built, why we built it that way, what went wrong, and how we fixed it._

---

## Part 0 — What Day 6 was for

By the end of Day 5, the bot could read a CV and turn it into a structured profile. But there was nothing to match that profile against. The `jobs` table had existed since Day 2, with an `embedding` column and an HNSW index, and it was empty.

Day 6's job was to fill it with real job postings — cleanly enough that Day 7 can turn them into embeddings and Day 8 can score them against a candidate.

The plan sheet says it in one line: pick one source, normalize the data, validate it, remove duplicates, filter before spending money on AI.

That one line contains seven separate decisions. Each becomes a rule the rest of the project has to live with. This document goes through all of them, plus the things we got wrong along the way.

---

## Part 1 — A credential leak, again

Before writing any code, something happened worth recording.

The project zip uploaded at the start of Day 6 had `.env` inside it. That file holds the Telegram bot token, the Gemini API key, and the Neon database password. All three were revoked and replaced.

`.gitignore` excludes `.env` and `*.zip`. Git was clean. But whatever tool made the zip ignored those rules and packed the file anyway.

### This was the sixth leak, and they all look the same

| #    | How it leaked                                                               | What we were watching |
| ---- | --------------------------------------------------------------------------- | --------------------- |
| 1, 2 | `httpx` logged the request URL, and the Telegram token sits inside that URL | "don't print `.env`"  |
| 3, 4 | `.env` was open as a VS Code tab during a screenshot                        | "don't print `.env`"  |
| 5    | Same                                                                        | "don't print `.env`"  |
| 6    | A zip tool packed `.env` despite `.gitignore`                               | "don't print `.env`"  |

**Not one of the six was someone typing `cat .env`.**

Every leak came through a path nobody was watching. The rule "never print `.env`" is not wrong. It just points at the least likely door.

### The general lesson

**A secret escapes through whatever handles it by accident, not through whatever handles it on purpose.**

Logging handles URLs by accident. Screenshots handle open tabs by accident. Zip tools handle file trees by accident. None of those look like a place a secret would come from — which is exactly why they keep working.

### The fix for zips

Let the tool that already knows what is safe decide:

```powershell
git archive --format=zip -o ..\AI_JOB_HUNT_AGENT.zip HEAD
```

This packs exactly what Git tracks. Git does not track `.env`, so it cannot end up in the zip. No judgement needed.

This lesson came back later on Day 6 and changed a design decision — see Part 5.

---

## Part 2 — Picking a job source

The rule says exactly one source. The obvious question is "which is best". A more useful question turned out to be:

**Which properties, if wrong, would stay hidden until Day 8?**

Because those are the expensive ones.

### What actually makes a source usable

**1. Do their terms let us _store_ the data, or only _display_ it?**

Most job aggregators exist to send traffic back to themselves. Their terms usually allow showing results on your site, not building your own database of them. We are building a database. This is the only item on this list that cannot be answered by testing — you have to read the terms.

**2. Full job descriptions, or short excerpts?**

This is the one that would have hurt most on Day 8. Day 7 turns job text into embeddings. An embedding built from a short excerpt is much weaker than one built from a full posting — **and nothing about it looks broken**. You just get mediocre matches, which look like a scoring problem. We would have spent a day tuning weights when the real problem was the input.

**3. Is there a stable ID per posting?**

`jobs.external_id` cannot be null and is half of a unique constraint. If a source has no stable ID, you have to invent one — and then identity and deduplication become the same thing before you have decided what deduplication means.

**4. Does it actually have Indian jobs?**

Not "supports India" on a marketing page. Real result counts for real queries.

**5. Where does the API key travel?**

A key in a header is safer than a key in a URL. After Part 1, this got extra weight.

**6. What kind of quota?**

Monthly and hourly quotas behave completely differently while debugging. An hourly quota forgives a bad loop — it refills while you make tea. A monthly one does not.

**7. Is there a posting date?**

Without one, the freshness filter does not exist and expiry cannot work.

**8. Where does the job URL point?** At the employer, or through the aggregator's redirect.

### What we picked

**Adzuna, country code `in`.**

It wins on stable IDs, Indian coverage, posting dates, and instant free signup. Its weaknesses — short descriptions, key in the URL, duplicate postings — are all things we can design around _on purpose_ instead of discovering later.

### What we rejected, and why

**Jooble** — also free and covers many countries, but it is POST-only to `https://jooble.org/api/{key}`, putting the key in the URL path. It also returns snippets, not full descriptions. So: no better on the thing that mattered, and worse on credentials.

**Remote-only boards** (Remotive, RemoteOK, Arbeitnow) — these do give full descriptions, which is genuinely tempting. Rejected because they are remote-only or Europe-focused. This bot is for someone in NCR. **Full text about jobs the user cannot take is worth less than excerpts about jobs they can.**

**Scraping the full job page from `redirect_url`** — considered and dropped. Slow, breaks easily, and a terms-of-service question in a project that had just had a credential incident. Wrong day for it.

---

## Part 3 — Measuring the source before trusting it

On Day 5, three hours were lost to guessing. Four rounds of "change something and try again", each testing a wrong idea. The lesson written down that day was:

> When a failure has several possible causes, build the thing that separates them.

Day 6 used that lesson _before_ the failure instead of after.

### The isolation script

`scripts/source_isolate.py` makes three calls, each bigger than the last. Each one, if it passes, rules out a whole class of problem.

| Step | What it sends                       | If it passes, this is not the problem                 |
| ---- | ----------------------------------- | ----------------------------------------------------- |
| 1    | Search endpoint, **no credentials** | network, DNS, Adzuna being down                       |
| 2    | Credentials, 1 result, no filters   | wrong credentials, spent quota                        |
| 3    | The real query with filters         | everything above — only the query or coverage is left |

Step 1 _expects_ to be rejected. A 400 or 401 counts as a **pass** — being rejected proves a real server received the request and made a decision about it. The only real failure at step 1 is a connection error or a timeout.

The script prints status codes and counts. **It never prints a URL**, for the reasons in Part 1.

### What we learned by running it

Three runs, six API calls. Four findings. Three changed the design.

**Finding 1 — descriptions are cut off at exactly 500 characters.**

```
min 500   median 500   max 500
ending in an ellipsis: 27/27
```

27 postings, three different queries. Every single one was exactly 500 characters and ended in `…`.

This is not "descriptions happen to be short". This is a hard cut.

500 characters is roughly 100–125 tokens. Enough to say what a job _is_. Not enough to include a skills list, a requirements section, or an experience range. **This became a permanent constraint for Day 7 and Day 8** — see Part 16.

**Finding 2 — Adzuna requires every word in the search to match.**

| Search                      | City  | Results |
| --------------------------- | ----- | ------- |
| `machine learning engineer` | Noida | 3       |
| `machine learning`          | Noida | **23**  |
| `machine learning engineer` | Delhi | 1       |

Removing one word gave 7.6× more results.

**This reversed a decision we had already made.** The original plan was "send filters to the API, it saves quota". That is right for freshness and wrong for keywords.

Why: a narrow keyword throws away real jobs, and **you never see the jobs it threw away, so you cannot notice the loss**. A filter you cannot audit does not belong in a default.

**Finding 3 — city names are literal, there is no region grouping.**

`Delhi` returned _fewer_ results than `Noida`. There is no "NCR" umbrella. Each city is just a separate string, and no provider will connect them for you. This is why `normalize_location` exists — see Part 6.

**Finding 4 — two things that went right.**

`created` comes back as `'2026-08-20T12:35:52Z'` — proper ISO 8601, explicitly UTC. And `id` is a stable string. Both fit the existing database columns with no guessing. Two problems we did not have to solve.

### The isolation script had its own bug, and that bug is the best lesson here

The first version of the description check said:

```python
if median < DESCRIPTION_LENGTH_CONCERN:   # 500
```

The real median is **exactly 500**. So the condition was `False`. **The warning never printed — in exactly the worst case it was written to catch.**

Same shape as the Day 4 bug that Day 5 fixed: a check that stays quiet in precisely the situation it exists for.

The mistake was thinking of a cap as "a small number". A cap is not a small number. A cap has a signature: **every length identical, and every description truncated**. So we detect that instead:

```python
capped = min(lengths) == max(lengths) and truncated == len(results)
```

**The general lesson: a threshold written with `<` will miss the boundary, and the boundary is usually where the interesting case sits.** If you are looking for a cap, look for the cap's signature, not for a number near it.

---

## Part 4 — Where the code lives

`app/integrations/` is the only place a third-party client can go. But normalizing data is a _rule_, not a client call. So where does one end and the other begin?

The answer was already in the codebase. `app/integrations/gemini.py` does not hand back a Google SDK object — it hands back a validated `CVProfile`, one of our own classes. Everything above it knows nothing about Google.

We copied that shape:

```
app/schemas/job.py              RawJobPosting — the common shape
app/integrations/adzuna.py      AdzunaClient — knows Adzuna's field names
app/integrations/http_errors.py describe_http_error — safe error text
app/services/job_ingestion.py   validation, filters, dedup, orchestration
app/services/locations.py       city name folding, shared with Day 8
app/db/repositories/job.py      the only place job SQL is written
app/db/models/ingestion.py      IngestionRun, IngestionReject, IngestionStatus
```

**The join is `RawJobPosting`.** The client owns the network and Adzuna's field names. The service owns every rule.

### Making "adding a second source is easy" a real guarantee

It is easy to _say_ a second source will be easy. To make it true, the service depends on a `JobSource` **Protocol**, not on `AdzunaClient` by name:

```python
class JobSource(Protocol):
    @property
    def source_name(self) -> str: ...
    async def search(self, *, what="", where="", page=1, ...) -> SearchPage: ...
```

A Protocol instead of a base class, so a new source does not have to import anything from the service, and so a test fake is just a plain object.

**The smaller the Protocol, the less a second source has to copy.**

### Why the client returns its own failures too

```python
@dataclass(frozen=True)
class SearchPage:
    total_available: int
    postings: list[RawJobPosting]
    unparseable: list[UnparseableRecord]
```

The obvious design is to return only the postings that parsed. That is wrong.

If you drop the failures, a page where **every record failed to parse** looks identical to a page that was **genuinely empty**. Those two have opposite causes — one is our bug, one is a quiet day in the job market — and you fix them in completely different places.

So they are counted separately, all the way up to the run status.

Each record is also parsed on its own. One bad posting must not throw away the 49 good ones on the same page. That would turn one bad row from Adzuna into a run that produces nothing, and it would look like an outage.

---

## Part 5 — The credential that lives inside a URL

Adzuna sends `app_id` and `app_key` as **URL parameters**. So the URL itself is a secret.

We already set `httpx` and `httpcore` loggers to WARNING on Day 5. That is not enough here, because of one specific thing:

```
str(httpx.HTTPStatusError)
  → "Client error '401 Unauthorized' for url 'https://...app_id=X&app_key=Y'"
```

**The error message itself contains both keys.** So do `exc.request.url` and `response.url`.

That makes normal, careful-looking code dangerous:

- `logger.error("fetch failed: %s", exc)` → both keys in every log file
- `error_message = str(exc)` → both keys **saved permanently in the database**, to be read months later by someone who has no idea they are looking at a live credential

### The fix

`app/integrations/http_errors.py`:

```python
def describe_http_error(error, *, timeout_seconds=None) -> str:
    if isinstance(error, httpx.HTTPStatusError):
        return f"HTTP {error.response.status_code} {error.response.reason_phrase}"
    ...
```

It **deliberately returns less than the exception contains**. The part it throws away is the URL, and the URL is the dangerous part.

Same idea as `_format_errors()` in `gemini.py`, which reads only `code` and `message` from a provider error instead of printing the whole object.

**General rule: an error object is not automatically safe to print just because it is an error.**

### One more detail that is easy to miss

The client raises with `from None`, not `from error`:

```python
raise JobSourceError(safe) from None
```

A chained traceback prints the original exception — and the original exception's text has the URL in it. Cutting the chain keeps the keys out of a crash dump.

### A test that checks the problem still exists

```python
def test_the_raw_exception_really_does_leak() -> None:
    assert FAKE_APP_KEY in str(_status_error(401, "Unauthorized"))
```

If httpx ever stops putting the URL in the message, `describe_http_error` becomes unnecessary rather than wrong. Finding that out from a failing test is much better than keeping a workaround nobody can explain.

---

## Part 6 — What counts as "the same job"

You need a definition before you need code. And whatever you pick, you live with.

### Three layers

**Layer 1 — `(source, external_id)`.** Already a unique constraint from Day 2. Handles the everyday case: running ingestion twice with the same query.

**Layer 2 — `content_hash`.** SHA-256 of the normalized title + company + location.

Not the description, on purpose. Aggregators rewrite and cut descriptions, so two copies of one job would hash differently — the opposite of what we want.

Not the URL either. The same job arriving through several boards under several URLs is exactly the case this layer exists to catch.

**Layer 3 — reposts.** Our rule: **a repost is the same job.** The existing row gets refreshed, not duplicated.

The alternative was to include a date bucket in the hash, so a repost after N days counts as new. We rejected it for a product reason: this is an _alert_ bot, and the worst thing it can do is ping someone about a job they already said no to.

Day 11's `uq_notification_user_job` would catch that per user — but only _after_ we had already paid for the Day 7 embedding and the Day 8 scoring on a duplicate row. Refreshing stops it at the cheapest possible point.

### The limit, said out loud

This is **exact matching**. `"Senior ML Engineer"` and `"Senior Machine Learning Engineer"` at the same company will be two rows.

Fuzzy title matching is genuinely hard. Putting it here would make deduplication unpredictable in exchange for catching a minority of cases. So it is a known limitation, written down, not hidden.

### Why `normalize_location` is a service, not a helper

`app/services/locations.py` folds `gurugram → gurgaon`, `bangalore → bengaluru`, and strips the `"City, State, Country"` tail Adzuna sends.

It is a shared service because **two places have to agree**:

- **ingestion** — so "Gurgaon" and "Gurugram" produce one hash
- **matching (Day 8)** — so a user who typed "Gurgaon" matches a job that says "Gurugram"

If those two used different rules, the failure would be silent and one-sided. Deduplication would work fine. Matching would quietly score zero. Nothing would look broken.

It is deliberately small. This is a **spelling problem, not a geography problem** — it does not know Noida is near Delhi, and it should not learn. Distance is a different question with a different answer (a geocoder). Pretending a lookup table can answer it is how a small correct thing turns into a big wrong one.

Same shape as `PUNCTUATION_ALIASES` in `app/db/repositories/skill.py`.

### It proved itself on the very first run

Run 1 ingested into an **empty table** and still recorded **1 duplicate**.

Adzuna sent two records in the same fetch with the same title, company and location, but different `external_id`s — the same job from two different boards.

Layer 1 alone would have stored it twice, embedded it twice on Day 7, and shown it to the user twice. Layer 2 caught it inside a single fetch.

---

## Part 7 — What happens to a job that fails validation

Three choices: drop it silently, mark it on the `jobs` table, or store it separately.

**Dropping silently** fails the goal — being able to answer "why did this job never show up" three weeks later.

**Marking it on `jobs`** is tempting, and it is a trap. `jobs` is the table Day 8 scores against. A rejected row sitting in it must be filtered out by _every_ future query, and the one query that forgets fails silently.

This project has already lived that exact shape twice:

- `cvs.superseded_at` — added afterwards, because rows that no longer mattered looked identical to rows that did
- `profiles.skills` vs `cv_versions.extracted_profile` — two things that must never be mixed

A third was not worth saving a table.

**We chose `ingestion_rejects`** — a separate table with `run_id`, `source`, `external_id`, `stage`, `reason`, `raw_payload`, `created_at`. Nothing joins to it, so it cannot pollute matching.

### Two details worth explaining

**`stage` separates `normalize` from `validate`.** Different failures:

- **normalize** — we could not read the record at all. Almost always Adzuna changed their format.
- **validate** — we read it fine, but it is missing something we require. Our rule, possibly too strict.

Mixing them would leave you unable to tell "the provider changed" from "my filter is wrong" — fixed in completely different places.

**`reason` is a sentence, not a flag.** `_validation_problem()` returns _why_:

```python
return f"url does not look like a link: {posting.url[:80]!r}"
```

Three weeks later, "validation_failed" tells nobody anything. The sentence does.

**`raw_payload` is kept** because when Adzuna changes their format, that stored payload is the only evidence of what changed.

### Filtered-out jobs are counted, not stored

A job outside the freshness window is not junk. Storing thousands of perfectly good jobs we simply did not ask for would cost more than the count is worth — and the count still answers "did anything get dropped here".

### One rule we may want to revisit

A posting with no `posted_at` is **rejected**. Not because it is useless, but because it could never expire — it would sit active forever. A choice with a cost, written at the place where it is made rather than left implied.

---

## Part 8 — "A success status is not success"

On Day 4, extractions that produced completely empty results were marked `complete`. Day 5 fixed it by adding `EMPTY` — a state meaning "it worked and said nothing" — and refusing to overwrite good data with it.

Day 6 has the exact same trap, and it is worse. **An ingestion run can end with zero new jobs for six different reasons:**

| What happened                         | Is it fine?              | Status           |
| ------------------------------------- | ------------------------ | ---------------- |
| Source sent nothing                   | Maybe                    | `NO_RESULTS`     |
| Records came, none survived our rules | **No**                   | `ALL_REJECTED`   |
| Records came, all already stored      | **Yes — this is normal** | `COMPLETE`       |
| Records came, all too old             | Maybe                    | `COMPLETE`       |
| Source errored                        | **No**                   | `SOURCE_ERROR`   |
| Quota spent                           | **No**                   | `QUOTA_EXCEEDED` |

Only one is healthy — and it is the most common one. On most days, almost everything is a duplicate.

**A single "0 jobs added" collapses all six into the number you look at first and the number that tells you least.**

`ALL_REJECTED` is the important one. It is the direct copy of `EMPTY`. Without it, the day Adzuna adds a field and our parser starts rejecting everything, the run says `complete, 0 new jobs` — identical to a quiet Tuesday.

`QUOTA_EXCEEDED` is separate from `SOURCE_ERROR` because the right reaction differs. A temporary failure invites a retry. A spent quota does not — retrying burns what is left and hides the real cause. Same lesson `gemini.py` learned by not retrying 429s, and it matters _more_ here because Adzuna's quota is monthly.

### The funnel, and the assert

The run record stores the whole funnel, not a summary. Every record leaves through exactly one counter:

```
records_fetched == normalize_failed + validation_failed
                   + filtered_out + duplicates + inserted
```

And we **assert** it at the end of every run:

```python
assert counters.records_fetched == counters.accounted_for(), (
    f"funnel does not balance: fetched={...} accounted={...}"
)
```

An assert, not a log — on purpose. A record lost between counters would otherwise show up only as a number being slightly lower than expected, which nobody notices.

**A wrong number that keeps running is worse than a crash, because people trust it.**

---

## Part 9 — Expiry, and why it needs a safety catch

`jobs.is_active` existed since Day 2 and nothing ever set it to `False`.

The requirement was: _show a reposted job again if the vacancy is still open, otherwise remove it._

Problem: **Adzuna never tells us a vacancy has closed.** So the requirement as stated cannot be built. The closest honest version:

**`last_seen_at`** — updated every time a run sees the posting. A job not seen for longer than `job_retire_after_days` (21) becomes `is_active = False`.

Rows are never deleted. A retired job keeps its history and — after Day 7 — its embedding, so a job that comes back costs nothing to revive. `mark_seen` also sets `is_active` back to `True`: a job that reappeared is open again, and leaving it retired would hide a live vacancy behind a decision made about older silence.

`job_retire_after_days` (21) is longer than `adzuna_max_days_old` (14) on purpose. The freshness window controls what comes _in_. The retire window controls what goes _out_. Setting them equal would retire every job the moment it aged out of the query that finds it.

### The safety catch

This needed the most thought.

Retirement guesses "closed" from "not seen recently". **That guess is only valid if we have actually been looking.**

If ingestion has not run for a week — laptop off, quota spent, script forgotten — then _nothing_ has been seen recently. Retiring on that basis would mark the entire table inactive in one pass, without a single vacancy having closed.

So retirement requires at least one successful run in the last few days:

```python
recent_successes = await run_repo.successful_runs_since(source, now - interlock_window)
if recent_successes == 0:
    logger.info("Skipping retirement: no successful run in the last %s days, "
                "so 'unseen' carries no information.")
    return 0
```

**No evidence means no conclusion.**

And what this still cannot do, written into the column's own docstring so the next reader finds it:

> Our queries are keyword-scoped, so a job missing from today's results may simply not have matched today's search. **Unseen is a guess at closed. It is the best guess available, not a fact.**

---

## Part 10 — Filters, and the decision we reversed

Filtering before AI saves money on **Day 7's budget**, not Day 6's. Every job filtered out is an embedding call not made, a row Day 8 does not score, and quota not spent.

Two kinds of filter, and the split ended up different from what we planned.

**Sent to the API (saves quota): freshness.** `max_days_old` goes in the request. A job the API never sends costs nothing. Bigger saving, and safe.

**Not sent to the API: keywords.** This is the reversal. Finding 2 in Part 3 measured that one extra word cut results by 7.6×, and the dropped jobs are invisible — you never see them, so you cannot miss them. Role matching moved to Day 8's title signal instead, where it can be _scored_ rather than silently applied.

**Checked again locally: freshness.** We re-check the date after the response arrives, even though we asked the API for it. Not redundant — it checks whether Adzuna actually honoured the parameter, and it is the only thing that would notice if they stopped.

**A filter you trust without verifying is a filter that can silently stop working.**

### Domain neutrality

`ADZUNA_QUERY_KEYWORDS` and `ADZUNA_QUERY_LOCATIONS` default to **empty, which means no filter at all** — every field of work, tech and non-tech, across the whole country.

A default list of tech keywords was written and then deleted. A default is an assumption about who the product is for, and **a default that works is the hardest kind to notice and remove.** Anyone who wants to narrow it edits `.env`, never code.

One implementation detail: these settings are `str`, not `list[str]`. `pydantic-settings` reads a list-typed field from the environment as JSON, so `ADZUNA_QUERY_KEYWORDS=python,sales` would crash at import instead of doing the obvious thing. A string plus an explicit split is less clever and surprises nobody.

And empty parses to `[""]`, not `[]` — one empty keyword, which the client turns into a request with no `what` at all. That is what makes "no configuration" mean _no filter_ instead of _no queries_.

### The trade-off, honestly

With no keyword and `sort_by=date`, page 1 is "the 100 newest jobs in India, any field". For a demo against an ML-focused CV, that may surface very few relevant matches — **and that would look like a scoring bug when it is actually an input choice.**

The architecture stays neutral. The narrowing lives in `.env` and takes ten seconds to apply before a demo.

---

## Part 11 — How ingestion is triggered

Manually: `python -m scripts.ingest_jobs`.

The thing that would make Day 10 (APScheduler) _harder_ is putting logic in the script. If the script were thirty lines of orchestration, Day 10 would mean either shelling out to a subprocess or rewriting it.

So the real unit is `run_ingestion(source, ...) -> IngestionResult` in the service. The script is a thin wrapper. Day 10 becomes: import the same function, register it with the scheduler.

### Transactions

`run_ingestion` **opens its own transactions and takes no session** — exactly like `extract_cv`, for exactly the same reason learned exactly as painfully.

Holding a transaction open across a call to a third party lends a database connection to somebody else's latency. Neon closes it with `IdleInTransactionSessionTimeout` while you wait.

Three phases:

1. Short transaction: create the run row, commit.
2. **No session open.** All HTTP calls happen here.
3. Short transaction: process records, write jobs and rejects, finish the run.

`--dry-run` prints the plan and the API call budget without spending a call. Useful precisely because the budget is monthly.

### `run.py`, built before it bit

Day 5 left a problem: `uvicorn app.main:app --reload` kills background tasks whenever you save a file, and without `--reload` it crashes on Windows with `Psycopg cannot use the 'ProactorEventLoop'`.

Day 6 as a script does not need a fix. **Day 10 does** — APScheduler runs inside the FastAPI process, and no version of Day 10 can use `--reload`.

So `run.py` was written now. Twelve lines. It sets `WindowsSelectorEventLoopPolicy` **before importing uvicorn**:

```python
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn  # noqa: E402 — imported after the policy on purpose
```

The import order _is_ the file. Moving `import uvicorn` back up with the other imports would silently bring back the crash. That is written in the module docstring, because otherwise it looks like a lint mistake rather than a decision.

---

## Part 12 — What got built

**New files:**

| File                                  | What it does                                           |
| ------------------------------------- | ------------------------------------------------------ |
| `run.py`                              | Windows-safe app entry point, no `--reload`            |
| `app/integrations/http_errors.py`     | `describe_http_error` — error text with no credentials |
| `app/integrations/adzuna.py`          | `AdzunaClient`, `SearchPage`, the two error types      |
| `app/schemas/job.py`                  | `RawJobPosting` — the shape all sources produce        |
| `app/db/models/ingestion.py`          | `IngestionRun`, `IngestionReject`, `IngestionStatus`   |
| `app/db/repositories/job.py`          | `JobRepository`, `IngestionRunRepository`              |
| `app/services/locations.py`           | `normalize_location`, `LOCATION_ALIASES`               |
| `app/services/job_ingestion.py`       | `run_ingestion`, the funnel, dedup, expiry             |
| `scripts/source_isolate.py`           | Three-step source diagnostic                           |
| `scripts/ingest_jobs.py`              | Manual ingestion CLI                                   |
| `alembic/versions/d7a3f1c92b40_...py` | The migration                                          |
| `tests/test_source_isolate.py`        | 13 tests                                               |
| `tests/test_job_ingestion.py`         | 42 tests                                               |

**Changed:** `app/core/config.py`, `app/db/models/job.py`, `app/db/models/__init__.py`, `app/db/repositories/__init__.py`, `app/services/__init__.py`, `tests/test_models.py`, `.env.example`.

**Numbers:** 145 tests passing (103 at the start of Day 6). Six migrations, head `d7a3f1c92b40`.

### The migration was written by hand

We did **not** use `alembic revision --autogenerate`.

Autogenerate cannot see the two HNSW indexes. They were created with raw `op.execute()` in migration `563b5bb86690`, because HNSW cannot be expressed through SQLAlchemy's `Index()`. So they are missing from `Base.metadata`, and autogenerate has twice tried to drop them.

A hand-written migration cannot make that mistake.

One change in it has a real behavioural effect rather than just a structural one: `content_hash` went from a plain index to a **unique constraint**. That is what makes a repost update an existing row instead of inserting a new one. It stays nullable, and Postgres treats NULLs as distinct under a unique constraint, so older rows without a hash do not collide.

---

## Part 13 — Verification

### The migration reverses cleanly and the indexes survive

```
alembic upgrade head       → d7a3f1c92b40
check_indexes              → Both HNSW indexes present
alembic downgrade -1       → 4b481a8ea241
check_indexes              → Both HNSW indexes present
alembic upgrade head       → d7a3f1c92b40
check_indexes              → Both HNSW indexes present
```

### Real ingestion against the real API

**Run 1** — empty table:

```
status: complete
records_fetched   100
inserted           99
duplicates          1   ← two boards, one job, caught in a single fetch
```

**Run 2** — immediately after:

```
status: complete
records_fetched   100
inserted            0
duplicates        100
```

```sql
SELECT count(*) FROM jobs;  → 99
```

**The row count did not double.** This is the deduplication proof.

**Run 3** — a nonsense keyword:

```
python -m scripts.ingest_jobs --keywords "qwertyuiopasdf"

status: no_results
records_fetched     0
pages_fetched       1   ← not 2
```

Two things confirmed. `no_results` is distinguishable from `complete` — which matters, because Run 2 and Run 3 both had `inserted: 0` and look identical in the number you check first. And the loop stopped after an empty page instead of asking for page 2 — one API call saved from a monthly budget.

### A note on `filtered_out`, which is always 0

`filtered_out` has been 0 on every real run, and that is **expected**, not a bug.

Two settings together cause it: `sort_by=date` means we read the _newest_ postings first, and `adzuna_max_pages_per_run=2` means we only read 100 of them. The newest 100 jobs in India cannot be 14 days old — India indexes thousands of jobs a day.

So the local freshness re-check is a **guard**, not a filter expected to fire. It exists to notice if Adzuna ever stops honouring `max_days_old`.

It becomes reachable if `adzuna_max_pages_per_run` or `adzuna_results_per_page` is raised, or if `adzuna_max_days_old` is shortened. **Raising the page cap is exactly what will happen before the Day 12 demo**, so this path is not dead code — just not reachable under today's configuration.

Covered by unit tests. Never exercised against live data yet.

### One thing not yet verified

**The `SOURCE_ERROR` path has not been proven end-to-end.** Unit tests cover it — a 500 raises `JobSourceError`, a 429 raises `JobSourceQuotaError`, and neither message contains a credential — but it has never been seen writing `source_error` into `ingestion_runs` for real.

To close it: temporarily break `ADZUNA_APP_ID` in `.env`, run once, check the run row says `source_error`, and **check that `error_message` contains no `app_id=`**. That last assertion is the one that matters and the one unit tests can only simulate. Then restore `.env`.

_(Attempted once; the run came back `complete` because the `.env` edit did not take effect. Still outstanding.)_

---

## Part 14 — Data quality problems found after ingestion

Once 99 real jobs were in the table, four problems appeared that Day 6 does not solve. Recorded here so Day 8 does not rediscover them.

**1. Recruiter spam.** One description reads:

> "Greetings of the day I am Arumugam Veera, reaching out to you regarding an exciting career opportunity... You can also connect with me on LinkedIn: linkedin.com/in/arumugamv/"

That is a person advertising themselves, not a job posting. The title was also `Senior ArificiaI Intelligence & Machine Learning Engineer | Lead | Architect` — misspelled, three roles crammed together.

**2. One company dominating.**

```
Vrinda International   11
Weekday AI              8
JobCrexa                4
```

23 of 99 jobs from three staffing consultancies. On Day 11, a user could receive 11 alerts from one agency.

**3. Useless locations.**

```
India                     23   ← no city at all
Bazargate, Mumbai          3   ← a neighbourhood
Hussainialam, Hyderabad    2
```

For 23 jobs, Day 8's location signal (15% of the score) has nothing to work with.

**4. Adzuna is an aggregator**, so listing quality is whatever the underlying boards accept. Some of these will be low-quality or scam postings.

### Why Day 6 does not fix any of this

Two different questions:

- **Field validation** — _can we store this?_ URL present, title present, date present. **Day 6's job, and done.**
- **Quality** — _should we show this?_ **Day 8's job.**

Filtering on quality at ingestion time would be wrong for two reasons. First, once dropped, you cannot tell how much was dropped correctly. Second, a "spam" guess can be wrong — Walmart and Deloitte also post through consultancies.

**Recommendation for Day 8: use these as score penalties, not as filters.**

- description contains `linkedin.com/in/` or "Greetings of the day" → lower the score
- `location == "India"` → location signal scores 0, but no penalty
- Day 11: cap alerts from one company per user, alongside `uq_notification_user_job`

---

## Part 15 — Lessons worth carrying forward

**1. A secret escapes through whatever handles it by accident.** Six leaks, none from `cat .env`. Logging, screenshots and zip tools all touch secrets incidentally. Guard the accidental paths, not just the deliberate one.

**2. An error object is not safe to print just because it is an error.** `str(httpx.HTTPStatusError)` contains the URL. So does the chained traceback — which is why `from None` matters. Write the redacting formatter before the first `logger.error`.

**3. A threshold written with `<` misses the boundary, and the boundary is where the interesting case lives.** `median < 500` stayed silent at exactly 500. Detect the _shape_ of the thing, not a number near it.

**4. Measure the provider before designing around it.** Three probe runs, six API calls, four findings — one reversed a filtering decision and one changed what Day 7 can expect. All four would otherwise have shown up on Day 8 as "the matching seems bad".

**5. Build the thing that separates causes before the debugging spreads.** `source_isolate.py` was written before the first ingestion attempt, not after the first confusing one. Day 5's three lost hours paid for that lesson.

**6. When "nothing happened", list the reasons before writing the status.** Six different causes produce `inserted = 0`, and only one is healthy. A boolean or a single count cannot carry that.

**7. Assert invariants that would otherwise fail as a slightly-wrong number.** The funnel check catches a lost record. Without it, the loss shows up as a count being a bit low — invisible.

**8. A guess needs a safety catch when its assumption can quietly become false.** "Unseen means closed" is only true while we are looking. Without the catch, a week away would have retired the entire table.

**9. Two users of one rule must share one implementation.** `normalize_location` is used by dedup and by Day 8 matching. Separate copies would fail on one side only, and silently.

**10. An empty default is a real design choice.** A default list of tech keywords _works_ — and a default that works is the hardest kind to notice and remove later.

**11. Two explanations for the same observation means you have not finished checking.** When every job's `posted_at` clustered inside one hour, the first guess was "Adzuna's date field is unreliable". The real cause was `sort_by=date` plus a 2-page cap — our own configuration. The earlier probe data (dates spread across 11–13 days) already contained the answer. **The same mistake `source_isolate.py` exists to prevent, made while reading its own output.**

---

## Part 16 — Where things stand, and what Day 7 can assume

### Day 7 can rely on

- **`jobs` holds normalized postings from one source (`source = 'adzuna'`).** Every row has a non-null `title`, `url`, `external_id`, `posted_at` and `content_hash`.
- **`posted_at` is timezone-aware UTC.** Adzuna sends `Z`-suffixed ISO 8601 and it is converted at the boundary. No naive datetimes reach the database.
- **Rows are unique** on `(source, external_id)` **and** on `content_hash`. A repost refreshes rather than duplicates.
- **`description` is cleaned** — HTML stripped, entities decoded, whitespace collapsed, trailing ellipsis removed.
- **`embedding` is `NULL` on every row.** That is Day 7's job. It is nullable so ingestion never blocks on an API call.
- **`last_seen_at` is set on every row** ingestion touches, and `is_active` is `True` unless retired.
- **Both HNSW indexes survive**, checked after upgrade, downgrade and upgrade again.
- **`run_ingestion(source, ...)` is callable from anywhere** and owns its own transactions. Do not wrap it in `session_scope()`.

### Day 7 must NOT assume

- **Descriptions are full postings.** They are **cut off at exactly 500 characters**, measured across 27 samples. An embedding built from `description` alone will be weak. **Build the embedding text from `title + company + location + description`** — the title carries the most signal when the body is an excerpt.
- **`job_skills` is populated.** It is **empty**, deliberately. Pulling required skills out of a description is parsing/LLM work and was out of scope.
- **`is_active = False` means the vacancy is closed.** It means unseen for 21 days while ingestion was running. A guess, not a fact.
- **Jobs are relevant to any particular user.** Ingestion is **global and domain-neutral**. Per-user relevance is Day 8's job.

### Risks carried forward

**1. Nobody owns job-skill extraction, and Day 8's biggest signal depends on it.**

The scoring table gives **skill match 30% weight** — the largest single component. But the plan sheet assigns job-skill extraction to no day at all. Day 6 says normalize and validate. Day 7 says embeddings. Day 8 says _calculate_ skill match, assuming the skills are already there.

They are not. And they have to line up with `skills.normalized_name`, the same catalog `profiles.skills` uses, or the 30% signal scores zero for every pair.

**This needs an owner on Day 7 or early Day 8.** Finding it on Day 8 morning would cost the day.

Two things make it harder: descriptions are 500-character excerpts that often contain no requirements list; and the skills catalog is currently tech-heavy (`pytorch`, `ansys fluent`, `cpp`) while ingestion is now domain-neutral, so non-tech skills will need to enter it too.

**2. The `SOURCE_ERROR` path is unproven end-to-end.** See Part 13.

**3. Data quality is unhandled.** See Part 14. Recruiter spam, one company dominating, and 23 jobs with no city.

**4. Quota is monthly.** Roughly 1,000 calls. Six spent on probes, about ten on ingestion runs. `adzuna_max_pages_per_run` is capped at 2 on purpose — raise it only once ingestion is known to work, and expect `filtered_out` to start firing when you do.

**5. Carried from Day 5, unchanged.** User 2 has 24 `cvs` rows, mostly `failed` from the Gemini investigation, all but the newest stamped `superseded_at`. cv_id 19 is stuck at `extracting`, claimed by a process `--reload` killed; it is superseded, so nothing will ever reclaim it. Both noisy, both explained, no cleanup path.
