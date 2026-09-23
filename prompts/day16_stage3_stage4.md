# Day 16 — Stage 3 (live verification) and Stage 4 (records)

Continue from the Day 16 work already in the tree: score_and_notify_user,
TRIGGER_SOURCE_PREFERENCES, the coalescing in-flight guard, the
crash-recovery finally, the onboarding completion-ordering fix, and the
logging hardening. CLAUDE.md is still the rulebook. Stop and report after
every step marked STOP.

## HARD RULES

1. NEVER read, print, cat, or grep .env. Never ask me for any database URL.
2. Scripts run as `python -m scripts.<name>`.
3. SQL goes in a .sql file, run with `python -m scratch_query q_name.sql`.
   scratch_query.py COMMITS writes. Never put SQL on the command line.
4. Do not change any threshold, weight, floor, or _GATE_WINDOW.
5. Anything that writes to the production database or can send a real
   Telegram message needs my explicit "yes" first. Ask, then wait.
6. Report before and after as two separate reads. Never a computed diff.
7. I do the phone steps. You never start or stop run.py yourself.

## STAGE 3 — Live verification on user 13

### 3.0 Precondition — DONE (see results above)

### 3.1 Read-only checks

- `Get-ScheduledTaskInfo -TaskName AIJobHuntAgent | Select-Object LastRunTime, LastTaskResult, NextRunTime`
  and the same for AIJobHuntAgentIngestion. 267009 means a run is in
  progress: stop and tell me. Also stop if either next run is less than
  2 hours away.
- Record, as separate reads, the current maximum id of scoring_runs,
  notifications, and agent_runs. These are the "before" values.
- Read user 13's user_preferences row: target_roles, preferred_locations,
  min_experience_years, max_experience_years, remote_only,
  notification_threshold. Expected threshold 0.6. Record the exact row;
  cleanup restores it.
  STOP. Report.

### 3.2 Restart the bot (my step)

Tell me to stop the running run.py and start exactly one new one. I confirm
one "Application started" line and no bind error on port 8000. Two
python.exe processes is normal (CLAUDE.md §1); two bots is not.

### 3.3 Clone job 593 — ASK FIRST

Same pattern as job 694 on 2026-09-23. external_id new
(user13-synthetic-day16-<yyyymmddhhmm>), content_hash new, is_active true,
url stays https://example.invalid/..., model fields stay synthetic:...,
all job_skills rows of 593 copied, in one statement. Report new job id and
skills copied. Then a separate fresh read: is_active, is_excluded,
embedding IS NOT NULL, skills_extracted_at IS NOT NULL, skill count.
STOP. Report.

### 3.4 Score and preview

- `python -m scripts.score_jobs --user-id 13`. Report NEW recommendation
  rows separately from UPDATED ones.
- Do NOT run --explain: it re-runs scoring and adds a scoring_runs row.
  Read the stored recommendations row instead.
- `python -m scripts.send_test_notification --user-id 13` WITHOUT --send.
  The new job must show ELIGIBLE at rank 1. Only the gate line is proof;
  this tool lists blocked jobs under "would send" too.
- Record max scoring_runs.id AFTER this step. 3.6 compares against it.
  STOP. Report.

### 3.5 Prediction

Before I touch my phone, write exact numbers for 3.6 and 3.7: new
notifications rows, their status and trigger_source, new scoring_runs rows,
Telegram messages, which users receive anything.
STOP.

### 3.6 Threshold 0.60 -> 0.65 — ASK FIRST, I do it on my phone

Wait at least 90 seconds after I confirm, then as separate reads:

- notifications for (13, new job): id, status, trigger_source, sent_at
- ALL notifications with id above the 3.1 max: id, user_id, job_id,
  status, trigger_source. Anything for a user other than 13 is a failure.
- max scoring_runs.id vs the value recorded at the end of 3.4.
  STOP. Report actuals next to the prediction.

### 3.7 Threshold 0.65 -> 0.60 — I do it on my phone

Wait at least 90 seconds, repeat the three reads. Expected: no new
notification rows, no message, no new scoring_runs row.
STOP. Report actuals next to the prediction.

### 3.8 Cleanup

Read id, external_id, is_active for every source = 'synthetic_test' row.
ASK, then deactivate every active one. Read again. Report both reads.
Confirm user 13's preferences match the 3.1 row.
A mismatch anywhere in Stage 3 is a finding. Record it, do not fix it.
STOP. Report.

## STAGE 4 — Records

New CLAUDE.md section "13. Day 16" with "Closed by Day 16" and "Open after
Day 16" in the existing style. PROJECT_STATUS.md §10 new item. CLAUDE.md §6:
test counts with and without a database as separate numbers; Alembic head
verified with `alembic heads`.

Closed (only what Stage 3 confirmed):

- Preferences edits trigger an instant run, trigger_source 'preferences'.
  Roles/locations rescore; threshold is deliver-only; experience triggers
  nothing.
- score_and_notify_user is the one shared implementation for onboarding and
  preferences, with an in-process coalescing per-user guard.
- Onboarding completion-ordering defect fixed. Why it survived: the Day 15
  live verification called _try_instant_recommendation directly for users
  whose preferences were already filled in, so the real ordering was never
  exercised.
- /update_cv already reached the instant path; now pinned by a test.
- Integration tests ran on a real Neon test database on Windows (41 passed).
- Credential-leak near-miss caught by tests: logger.exception in
  score_and_notify_user would have logged a rejected bot token
  (python-telegram-bot's InvalidToken embeds it). Fixed, plus the embed-step
  log in _try_instant_recommendation. logs/: 18 files, 0 contain the string.
- Integration conftest now blocks any real TelegramNotifier construction.

Open:

- Cross-process duplicate send vs the nightly run_agent.py; the partial
  unique index fixes bookkeeping, not the second message. Precedent:
  CVRepository.claim_for_extraction. Revisit if a duplicate is observed or
  before adding a second always-on process.
- Experience preference is stored and never read by scoring. Owner decides.
- /update_cv runs are tagged trigger_source 'onboarding'. Undecided.
- normalize_location locality limitation: 2026-09-23, 7 Delhi jobs scored
  location 0.0 for user 13 ("Sansad Marg, New Delhi", "South Delhi, Delhi",
  "Maurya Enclave, North West Delhi"). Remote jobs score 1.0 regardless of
  city, which is a rule. Day 6 rule "two users of one rule share one
  implementation" constrains any fix.
- Pre-existing log hygiene: notification_delivery.py:577, 623, 634
  (logger.exception); onboarding handler :348 and preferences handler :85
  (logger.debug exc_info=True).
- Unexplained: scoring run 33 printed no jobs_remote line, run 34 printed
  jobs_remote 5, same inputs.
- Environment flake: test_the_notify_branch_delivers_and_records_an_attempt
  failed once with "server closed the connection unexpectedly" against Neon;
  not reproduced on two reruns.

§1 rows to add:

- score_jobs --explain is not read-only; it writes a scoring_runs row.
- send_test_notification sends the top unsent job even when the gate says
  blocked; manual_test rows are not evidence the gate passed.
- trigger_source 'preferences' rows at arbitrary times of day are expected.

Do not commit anything. Do not delete untracked files. Run
`git status --short --untracked-files=all` and list every ?? line with a
recommendation (commit, gitignore, delete). Expected: scratch_query.py,
scratch_users.py, check_url.py, q__.sql files, prompts/day16__.md, and
files (1).zip (CLAUDE.md incident 11 involved a zip with this exact name;
list its entry names with verify_archive.py, never extract it).
STOP. Report.
