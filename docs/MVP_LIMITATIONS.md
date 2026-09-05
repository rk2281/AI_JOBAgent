# MVP Limitations

What this system does not do, cannot currently prove, or does in a way
somebody should decide about deliberately. Nothing here is hidden in a
footnote elsewhere.

Ordered by how much it should worry you.

---

## 1. Credentials have leaked three times and have never been rotated

`GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `ADZUNA_APP_KEY` and
`DATABASE_URL` have left the machine in three separate shared archives.
Two of those archives also contained `storage/cvs/` — real candidate CV
PDFs for users 2, 3, 10 and 12.

**A key can be rotated. A leaked CV cannot be recalled.**

Rotation is a human task and is not done. Adzuna first: its `app_id` and
`app_key` travel as **query parameters**, so they appeared in logged
request URLs for months. Gemini's key travels in a header and was never
printed.

`pack.ps1` was not broken in any of the three incidents. It was
bypassed — somebody used Explorer's "Compress to zip" instead.
`scripts/verify_archive.ps1` now exists to catch that, and it correctly
condemns the most recent archive. It cannot intercept a right-click; it
can only refuse an archive somebody remembers to check.

## 2. The notification gate has never fired on real data

`notify_eligible` is 0 and always has been. 242 of 294 pairs sit at
`weight_covered` exactly 0.50 against a floor of 0.55.

This is the gate **working**, not failing — see
`docs/MATCHING_AND_SCORING.md`. But it means the product's core promise
has never been delivered to a real person by the automated path. One
notification has been sent, with `trigger_source = manual_test`.

The fix is a completed enrichment pass, which is blocked on Gemini
quota (below). **Do not lower the floor to make this go away.**

## 3. Enrichment is blocked on quota, so 40% of the model is inactive

Gemini's free tier is a daily quota granting roughly one call before 429. A full enrichment pass is ~97 calls and 27–84 minutes.

Consequently: 5 jobs enriched, of which 2 produced skills; 0 jobs with
experience bounds; **3 of 5 scoring signals active**. Skill (0.30) and
experience (0.20) abstain almost everywhere.

Job 81 is permanently locked out at the 3-attempt ceiling; only
`--retry-failed` reaches it. Seven more sit at 1 attempt from old 429s.

## 4. Three external services are unverified end to end

Adzuna, Gemini and Telegram were all unreachable during Day 12
verification. Every rule around them is tested; the sockets are not.
See `docs/TEST_RESULTS.md` for exactly what that leaves unproven.

## 5. The scheduler has never been observed to fire

`schedule_agent.ps1 -SelfTest` proves the interpreter, working
directory, log path and command line. It does not prove Windows starts
the task, that its credentials can read the repository, or that a log
lands with a real exit code.

`scripts/check_run_freshness.py` now answers the question that matters
— did a run _happen_ — independently of the mechanism. It is not
scheduled or alerted on; somebody has to run it.

## 6. The graph cannot run non-dry without Telegram

The `notify` node calls `run_notification_delivery()`, which builds a
real `TelegramNotifier`. `deliver_notifications` accepts an injectable
notifier but nothing threads one through. So a full non-dry
`ainvoke` always opens a socket.

Not fixed in Day 12: adding an injection path through the graph would
change production wiring to suit a test, and the graph deliberately
holds no such seams.

## 7. `logs/` is gitignored but not forbidden

Neither `pack.ps1` nor `verify_archive.py` blocks `logs/`, so neither
would complain about an archive containing it — and the archive this
work arrived in did contain it.

An unattended run log is the one file nobody reviews before packing,
and it may hold user ids, job titles and error text. This is pinned by
`test_logs_are_deliberately_absent_from_both_lists` so that adding it
becomes a deliberate change to three files rather than a silent drift.
**This needs a decision.**

## 8. Open decisions inherited from earlier days

- **The abstention asymmetry** is measured, documented and undecided.
  A job with _missing_ data can outrank one with _bad_ data.
- **`users_skipped_no_cv` counts three different things** and keeps its
  imprecise name deliberately — renaming would make historical rows
  unreadable against new ones.
- **`users_skipped_no_profile` can never be non-zero** through
  `run_agent.py`, so a zero there is evidence of nothing.
- **`scoring_runs` has no columns for the skip breakdown.** The
  counters live in `agent_runs` instead. Adding them remains undecided.
- **CV 24 reads `extraction_status = 'failed'`** while its extracted
  version exists and is embedded. Nothing downstream noticed;
  `profile_view.py` is the one consumer that reads it, i.e. what the
  user sees. Needs a decision, not a repair.
- **One company field holds three companies joined by `||`.** Exact
  match will never catch it.
- **The staffing-agency list may be incomplete** — 6 more candidates
  would take affected pairs from 29 to 35.
- **`state["errors"]` is never written**, so the graph's `failed`
  status is unreachable. Do not invent a writer to make it reachable;
  do not rely on it either.

## 9. Scope the MVP deliberately excludes

No web or mobile UI — Telegram is the entire interface. One job source.
One language. No CV rewriting or cover letters. No application
tracking. No learning from feedback: `user_feedback` accumulates and
nothing reads it back into ranking. No multi-tenancy, no rate limiting
on the bot, no retry queue for failed notifications, and no
notification digest — one message per job.

## 10. Operational gaps

- **No monitoring or alerting.** Every check is a script somebody runs.
- **No structured log aggregation.** Logs are files on one machine.
- **No backup or restore procedure** for the database.
- **`scripts/concurrent_claim_dryrun.py` is not a dry run.** It fires
  real Gemini extractions and has already left a CV in a bad state.
  "dryrun" in a script name is a promise of no writes; it should be
  renamed `concurrent_claim_probe.py`.
- **The Day 10 prompts are committed, unfolded and self-contradicting
  on purpose.** Whether to fold them is a human decision.
