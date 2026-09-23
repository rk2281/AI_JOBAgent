# Day 16b — Real-job match via preferences (read-only probe, then a live test)

Goal: find REAL, active, enriched technical jobs already in the database that
user 13 could receive if their `target_roles` / `preferred_locations` were
set to match, then set those preferences from my phone and confirm the real
job arrives through the instant path (`trigger_source = 'preferences'`, the
rescore branch, which Stage 3 did not exercise live).

This is a test of the product, not a change to it. Preferences are restored
at the end.

CLAUDE.md is the rulebook. Stop and report after every step marked STOP.

---

## HARD RULES

1. NEVER read, print, cat, or grep `.env`. Never ask me for any database URL.
2. Scripts run as `python -m scripts.<name>`. SQL goes in a `.sql` file run
   with `python -m scripts.query_file q_name.sql` (it COMMITS writes).
3. Do not change any threshold, weight, floor, anchor, `_GATE_WINDOW`, or
   `THRESHOLD_CHOICES`. Do not insert, edit, or activate any job.
4. Do not write to `user_preferences` yourself. I change preferences from my
   phone via `/preferences`. That is the path under test.
5. No Gemini calls anywhere in this task.
6. Never report a number you computed by hand. Every score comes from the
   project's own functions (`score_title`, `score_location`, `combine`,
   `is_notify_eligible`), called in-process.
7. I do each phone step FIRST, wait, and only then confirm to you. Do not
   run post-tap reads until I say the tap is done.

---

## STAGE A — Read-only probe. No writes of any kind.

Write `scripts/preference_match_probe.py` (read-only; never name it
"dryrun"; opens no Telegram client and no Gemini client). For user 13:

1. Candidate jobs: `is_active = true`, `is_excluded = false`,
   `source <> 'synthetic_test'`, `skills_extracted_at IS NOT NULL`, with an
   existing `recommendations` row for user 13, and NO `notifications` row
   for (13, job) with `status = 'SENT'` (uppercase label; CLAUDE.md §1).
2. For each candidate, keep the STORED skill, semantic and experience
   signal values and `semantic_raw` from `recommendations` unchanged. Those
   come from the CV and the job, and preferences cannot move them.
3. Recompute ONLY title and location with the real `score_title` and
   `score_location`, as if `target_roles = [job.title]` and
   `preferred_locations = [<the value normalize_location(job.location)
returns>]`, keeping `remote_only` as stored. Then run the real
   `combine()` (quality multiplier included) and the real
   `is_notify_eligible()` at each threshold in `THRESHOLD_CHOICES`.
4. Self-check first. For at least 3 candidates, recompute with user 13's
   CURRENT preferences and assert the result equals the stored
   `final_score` (tolerance 1e-9). If it does not match, stop: the probe is
   wrong, not the data.
5. Print one row per candidate, sorted by hypothetical final score
   descending, top 15: job_id, title (40 chars), location (30 chars),
   work_mode, stored semantic_raw, weight_covered, stored final_score,
   hypothetical title/location values, hypothetical final_score, and
   eligible at 0.6 / 0.7 / 0.8.
6. Also print, as separate numbers: candidates considered, candidates
   eligible at 0.6, and how many were excluded because they were already
   SENT.

Two things to state in the report, not fix:

- If `score_title` strips weak tokens so the job's own title matches
  nothing, say so for that job. Title then abstains.
- `normalize_location` takes the first comma segment (known, documented in
  `docs/MATCHING_AND_SCORING.md:209-223`). The "city" the probe uses may be
  a locality. Say which string I would have to type.

**STOP. Report the table, the three counts, and the self-check result.**

If zero candidates are eligible at 0.6, say so plainly and stop there. That
is a valid result: it means no real job can reach user 13 through
preferences alone, because the blocking gates (semantic, coverage) come
from the CV and the job data.

---

## STAGE B — Pick one job and predict

Pick the single best candidate eligible at 0.6. Tell me:

- the exact text to send for the role, and for the location, via
  `/preferences` (roles and locations are free-text edits);
- whether I should change roles first or locations first, and why. Each
  edit triggers its own rescore run. Predict what the FIRST edit alone
  produces, because it may send nothing, or it may send a different job;
- the prediction in exact numbers for each edit: new `notifications` rows
  (status, trigger_source, job_id), new `scoring_runs` rows (rescore means
  +1 per edit, unless coalesced), which users receive anything, how many
  Telegram messages.

Record, as separate reads, the current max ids of `notifications` and
`scoring_runs`, and user 13's full `user_preferences` row. Restore targets
come from this row.

Check `Get-ScheduledTaskInfo` for AIJobHuntAgent and AIJobHuntAgentIngestion.
Stop if either is running (267009) or due within 2 hours.

**STOP.**

---

## STAGE C — Live test (my phone)

I send the first edit, wait at least 2 minutes, then tell you what arrived.
Then you run, as separate reads:

- `user_preferences` for user 13
- notifications with id above the Stage B max: id, user_id, job_id,
  status, trigger_source
- max `scoring_runs.id`

Then the same for the second edit.

**STOP after each edit. Report actuals next to the prediction.**

---

## STAGE D — Restore (my phone)

I set roles and locations back to exactly the Stage B row. Each restore edit
triggers a rescore. Predict first: with the original preferences, nothing
new should be eligible (Stage 3 showed no real job clears all three gates at
0.6). Then read preferences, new notifications, and max `scoring_runs.id`.
Confirm the preferences row equals the Stage B row field by field.

**STOP. Report.**

---

## RECORDS

Add to CLAUDE.md's Day 16 section:

- Closed, if Stage C confirmed it: the roles/locations rescore branch of the
  instant path delivered a real Adzuna job live (job id, notification id).
- The probe script: what it reads, that it is read-only, its self-check.
- Any mismatch between prediction and actual, as a finding.

Do not commit. List new untracked files at the end.
