# Day 10, Part 3 — Amendment 1: the scheduler contradiction

This amends `prompts/day10_part3.md`, which you have already been given.
Everything in that document still stands **except** §2.2 and §2.4, which
are replaced below. `CLAUDE.md` §0 and §1 apply throughout as before.

Do this amendment's §A first, then continue the original document from
wherever you are.

---

## A. Fold this into the prompt file before writing code

`prompts/day10_part3.md` is the record of what was asked. It currently
contains an instruction that contradicts the plan, and a corrected copy
that pretends otherwise is worse than the wrong one.

So: edit `prompts/day10_part3.md` in place. Replace its §2.2 and §2.4
with §C and §D below, and add a short "Amendment 1" note at the top of
the file saying what was changed and why — two or three sentences, not a
changelog. Commit that edit **on its own**, before any code, so the
correction has its own hash and is not smuggled in with the work.

Then carry on.

---

## B. What was found, and why §2.2 was wrong to state a conclusion

§2.2 told you to write a Windows Task Scheduler registration script in
`pack.ps1` style. That was carried forward without checking it against
the plan or the codebase. Both disagree.

The plan spreadsheet's Day 10 row, read from the file rather than from
memory:

| Column | Value |
|---|---|
| Phase | Automation & Background Processing |
| Objective | Run job discovery and recommendation generation automatically. |
| Technologies | **APScheduler**, optional Celery, optional Redis |
| Deliverable | Automatic scheduled recommendation pipeline |
| Dependencies | Days 6-9 |
| Priority | High |

Three docstrings in this repository say the same thing, and they were
written as promises about Day 10 specifically:

- `scripts/run_agent.py:10` — Day 10 registers the compiled graph with
  APScheduler rather than dismantling this file.
- `scripts/ingest_jobs.py:9` — Day 10 is a matter of registering
  `run_ingestion` with APScheduler rather than a rewrite.
- `app/services/job_ingestion.py:565` — `scripts/ingest_jobs.py` today
  and APScheduler on Day 10 are two callers of one thing.

And the seam was reserved: `app/schedulers/` exists and is empty.
APScheduler is neither pinned in `requirements.txt` nor installed —
verified with `pip show`, not assumed.

**But the original instruction is not obviously wrong, and this is the
part to think about rather than resolve by deferring to the plan.**
§2.1 asks you to verify that `run_agent.py` exits non-zero on `failed`
or `degraded` so a scheduler can tell a problem from a quiet night
without parsing output. Under APScheduler the graph is invoked
in-process and that exit-code path never runs — so the mechanism the
plan names removes the property §2.1 is built on. The same applies to
the rest of §2.2's requirements: capturing an exit code, selecting the
`.venv` interpreter and setting the working directory are all
OS-scheduler concepts. And an in-process scheduler stops silently when
its host process stops, which is `CLAUDE.md` §0 in its purest form: a
dead scheduler and a quiet night produce identical evidence.

There is precedent for going either way. Day 9 decision 2 recorded
`embed_jobs` as absent from the plan's Day 9 row and called it a "plan
gap, not a code gap". `CLAUDE.md` §6 treats the spreadsheet as
authoritative for the scoring weights. Divergence is allowed here. What
is not allowed is diverging without saying so.

---

## C. Replaces §2.2 — Scheduling

This is a Windows machine (`E:\AI_JOB_HUNT_AGENT`, PowerShell, `.venv`).

**Decide the mechanism before writing it, and record the decision.** The
choice is between an OS-level scheduler (Windows Task Scheduler, driven
by a registration script in `pack.ps1` style) and an in-process
scheduler (APScheduler, as the plan and the three docstrings say). Do
not pick by deferring to either the plan or the original prompt. Argue
it, choose, and write the argument into `docs/Day_10_progress.md` before
the code.

Whichever you choose, four things must be answered in the record:

1. **§2.1's exit-code property.** State what happens to it under your
   choice. If it survives, say how. If it does not, say what replaces
   it — because "a scheduler can distinguish a problem from a quiet
   night" is the property that makes the whole task worth doing, and
   losing it silently is the failure §0 describes.
2. **Silent death.** State how a stopped scheduler becomes visible to a
   person. Both mechanisms can die quietly; they die differently.
3. **Placement, if APScheduler.** `app/schedulers/` is reserved and
   empty. Day 9 decision 1 put `langgraph` in `app/workflows/` on the
   reasoning that `app/integrations/` is for things making network calls
   on someone else's credentials. Apply that same reasoning here and say
   where it lands you. `CLAUDE.md` §4 layering still holds.
4. **The pin, if APScheduler.** It becomes a new **direct** dependency
   in `requirements.txt`, the first since Day 9. Part 1 established the
   transitive-versus-direct discipline and wrote the reasoning above the
   pin. Do the same, and note that Celery and Redis stay out — the plan
   marks both optional and neither has a caller.

The original functional requirements stand regardless of mechanism:

- Run inside the project's `.venv`, not whatever Python is on PATH.
- Working directory is the repo root, since `cv_storage_dir` resolves
  relative to it.
- Capture stdout, stderr **and the run's outcome** to a timestamped log
  under a gitignored directory. An outcome that is not recorded is an
  unattended run that reports nothing. If your mechanism has no exit
  code, this is where requirement 1 above gets paid for.
- Never `--dry-run`. A scheduled dry run is a scheduled no-op that looks
  healthy.
- A `-SelfTest` equivalent that proves the mechanism without arming
  anything. For a PowerShell script that is a switch, in `pack.ps1`
  style. For APScheduler it is a test or a script that registers the job
  against a clock it controls and asserts it fired — not a real wait.

Before writing a line to that log, apply `CLAUDE.md` §3: the run invokes
`app/integrations/adzuna.py`, whose credentials travel as query
parameters, and an unattended log is exactly the incidental
secret-handling that produced nine of the eleven incidents. State what
the log can contain on a failing run.

State plainly what your choice does *not* handle — overlapping runs,
missed runs while the machine was off, log growth. Do not solve all
three; name them and pick, with reasons. Note which of the three the
*other* mechanism would have handled for free, if any.

---

## D. Replaces §2.4 — The one real run

**Blocked until Task 0.a is confirmed.** Do not spend the run on
credentials scheduled for rotation. If rotation has not been done, stop
Task 2 at §2.3, say so in the record, and move to Task 3. That is a
finding, not a failure.

Spend it here. Trigger one full run through the scheduling mechanism you
built — the unattended path, not by hand. Then show the log file, the
outcome your mechanism records, the `agent_runs` row and the
`scoring_runs` row.

Predict all four before triggering it.

If the mechanism is APScheduler, "unattended" means the scheduler
process fired it on its own schedule, not that you called the registered
function directly. If you cannot demonstrate that inside one session,
say so rather than substituting a direct call and describing it as
unattended — that substitution is the exact shape of §0.

---

## E. After the amendment

Continue the original document. Task 1, Task 3, the records and the
commits are unchanged. The commit list gains one entry at the front:

0. Amendment 1 folded into `prompts/day10_part3.md` — no code.

Report its hash with the others. In the final report's section 5, the
scheduler choice is the decision to lead with, including what you
rejected and the cost of rejecting it.