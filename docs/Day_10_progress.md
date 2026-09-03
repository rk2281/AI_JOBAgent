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
