# Manager Demo

A 15-minute walkthrough. Every command here was executed on 2026-09-05
and produced the output shown.

**Run through it once beforehand.** Three stages need external services
that may be unavailable, and the demo is stronger if you say so upfront
than if it surfaces as a failure mid-sentence.

---

## Before you start

```powershell
.\.venv\Scripts\Activate.ps1
alembic current                 # expect c8e2a15f4b93 (head)
python -m pytest -q             # expect 706 passed, 23 skipped
```

Have two windows open: a terminal, and `psql` against the database.

---

## 1. The problem (1 min)

A job seeker checks four or five boards, re-reads the same postings,
and still misses the one that mattered. Job alerts don't help — they
match on keywords, so they send everything, and everything gets
ignored.

The hard part isn't finding jobs. It's deciding which of them is worth
a person's attention, and being able to say why.

## 2. The solution (1 min)

A Telegram bot that reads your CV once and then watches for you.
Nightly, unattended. It messages you only when a posting clears a bar —
and every recommendation carries the numbers that produced it.

## 3. Architecture (2 min)

```text
Telegram -> FastAPI -> CV intelligence -> profile
                                            |
Job boards -> ingestion -> dedup -> embeddings -> pgvector
                                            |
                              five-signal hybrid scoring
                                            |
                              LangGraph agent + gate
                                            |
                              Telegram -> feedback -> PostgreSQL
```

Two points worth making out loud:

- **One database.** Jobs and vectors in the same rows, so a semantic
  search is one join from a location filter. No second store to
  disagree with the first.
- **Deterministic where the rule is knowable.** The LLM turns a CV into
  structured fields. The scoring is arithmetic — reproducible,
  auditable, explainable.

## 4. Live flow (3 min)

```powershell
python -m scripts.e2e_verify --database-url postgresql+psycopg://.../jobagent_test
```

17 stages in 370 ms. Walk down the trace: CV uploaded, text extracted,
profile persisted, jobs ingested, funnel balanced, retrieval, scoring,
graph, notification, feedback.

Point at **`Jobs ingested   fetched=6 inserted=3 duplicates=1`**.
Six records became three jobs. One was the same posting re-listed under
a new id — caught by content hash, not by id. Two were rejected with
reasons stored in `ingestion_rejects`.

Point at **`Funnel balanced   6 == 6`**. Every record leaves through
exactly one counter, and that is asserted rather than assumed.

Say plainly: three stages read **STAND-IN**. The job board, the model
and Telegram were unreachable. Everything around them is production
code.

## 5. Explain one recommendation (3 min)

This is the part that lands. In `psql`:

```sql
SELECT job_id, rank, round(final_score::numeric, 4) AS score,
       semantic_score, skill_score, experience_score,
       location_score, title_score, weight_covered
FROM recommendations ORDER BY rank;
```

```text
 job_id | rank | score  | semantic | skill_score | experience_score | location | title | covered
--------+------+--------+----------+-------------+------------------+----------+-------+---------
      1 |    1 | 1.0000 |    1.000 |           1 |                1 |    1.000 | 1.000 |       1
      2 |    2 | 0.7000 |    1.000 |             |                  |    1.000 | 0.000 |     0.5
      3 |    3 | 0.0000 |    0.000 |             |                  |    0.000 | 0.000 |     0.5
```

Then:

```sql
SELECT match_reasons FROM recommendations WHERE rank = 2;
```

```text
["semantic similarity 0.9511 raw", "preferred location match",
 "title shares no words with any target role",
 "abstained: job lists no extractable skills",
 "abstained: job states no experience requirement"]
```

Three things to say about that table:

1. **The blanks are NULL, and NULL means "we couldn't look".** Job 2
   has no extracted skills, so the skill signal _abstained_ rather than
   scoring zero. Scoring it zero would rank a job down for having a
   short description rather than for being a bad fit.
2. **`covered` is why job 2 doesn't get sent.** Its score is 0.70, at
   the threshold — but it was computed from half the model. The
   notification gate requires 0.55 coverage, so it is refused. Job 1,
   scored on the whole model, is the only one that qualifies.
3. **`match_reasons` is stored per row**, including the abstains. A
   user asking "why this job?" gets an answer that was written down at
   the time, not reconstructed afterwards.

## 6. The agent (2 min)

```powershell
python -m scripts.run_agent --user-id 2 --dry-run
```

Eight nodes, three conditional edges. Explain what LangGraph actually
controls: **routing**, not logic. No scorable users stops the run
early. No qualifying recommendations skips notification entirely. Both
branches are tested in both directions — the notify edge was proven
before it ever carried a message.

```sql
SELECT id, started_at, finished_at, status, notify_branch, notify_eligible
FROM agent_runs ORDER BY id DESC LIMIT 5;
```

The row is opened **before** the graph runs and completed after, so a
run killed mid-flight leaves evidence rather than nothing.

## 7. The database (1 min)

One user, traced end to end:

```sql
SELECT u.id, c.extraction_status, cv.version,
       (cv.embedding IS NOT NULL) AS embedded,
       (SELECT count(*) FROM recommendations r WHERE r.user_id = u.id) AS recs,
       (SELECT count(*) FROM notifications n WHERE n.user_id = u.id) AS notes,
       (SELECT count(*) FROM user_feedback f WHERE f.user_id = u.id) AS feedback
FROM users u
JOIN cvs c ON c.user_id = u.id
JOIN cv_versions cv ON cv.cv_id = c.id
WHERE u.id = 1;
```

CV upload to feedback, every hop a real foreign key.

## 8. Feedback (1 min)

```sql
SELECT job_id, action, created_at FROM user_feedback ORDER BY created_at;
```

Three buttons on every notification. Contradictions are **kept**:
Interested then Not Relevant is two rows, not a correction. The gap
between "looked appealing in a notification" and "stopped looking
appealing once read" is exactly the signal a future re-ranking wants,
and it exists only while both rows do.

Be straight that nothing reads it back yet. The data is being collected
for a model that doesn't exist.

## 9. Automation (1 min)

```powershell
python -m scripts.check_run_freshness
```

```text
agent_runs rows    1
window             26 hours
age                4.0 hours
result             FRESH
```

Scheduled nightly through Task Scheduler, which survives reboots. And
this check answers the question that actually matters — _did the run
happen_ — by looking at the table the run writes, so it works
regardless of what does the scheduling.

## 10. Tests (2 min)

```powershell
python -m pytest -q
```

```text
706 passed, 23 skipped
```

```powershell
$env:TEST_DATABASE_URL = "postgresql+psycopg://.../jobagent_test"
python -m pytest -q
```

```text
729 passed
```

Explain the 23: integration tests against a real PostgreSQL with
pgvector. They skip without one, and a skip prints a green `s` that
reads like a pass — so both figures get reported.

---

## Questions you should expect

**"Is it actually sending notifications?"**
Not yet, on real data, and the reason is worth hearing. The gate
requires 55% signal coverage; 242 of 294 real pairs sit at exactly 50%,
because most postings don't list required skills or experience. The
gate is working. The fix is a completed enrichment pass, which is
blocked on API quota, not on code.

**"How do you know the matching is any good?"**
We don't, in the sense of measured precision against human judgment.
What we can show is that every score is reproducible, every input is
stored, and the ranking is explicable. Feedback is being collected to
answer this properly later.

**"What breaks first at scale?"**
The HNSW `ef_search` behaviour: below the `LIMIT` it silently returns
fewer rows than asked for. `run_scoring` already scales it to the pool
size. At 99 jobs Postgres uses a sequential scan and it doesn't bite.

**"Can I see it work on my own CV?"**
Yes, if the Telegram bot is running — but note that a full CV
extraction plus enrichment pass needs Gemini quota we may not have
today.

**"What worries you most?"**
Answer honestly: the credentials have leaked three times and have never
been rotated, and two of those archives contained real candidate CVs. A
key can be rotated; a CV cannot be recalled. That is the top item in
`docs/MVP_LIMITATIONS.md` and it is a task for a person, not for code.

---

## Do not

- Do not claim Telegram, Adzuna or Gemini are verified end to end. They
  are not. `docs/TEST_RESULTS.md` says exactly what is.
- Do not present 370 ms as a production latency. It excludes every
  network call, which is where all the real time goes.
- Do not present the demo scores as typical. The fixture is generous;
  real data produces nothing above the gate.
