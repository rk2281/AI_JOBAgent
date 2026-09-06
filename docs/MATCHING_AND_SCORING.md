# Matching and Scoring

The formula **as implemented**, read out of `app/services/scoring.py`
and `app/services/scoring_signals.py`. Where this document and the code
disagree, the code is right and this document is a bug.

---

## The five signals

| Signal     | Weight | Question it answers                                      | Source                             |
| ---------- | ------ | -------------------------------------------------------- | ---------------------------------- |
| Skill      | 0.30   | Do the job's required skills appear on the CV?           | `job_skills` vs `profiles.skills`  |
| Semantic   | 0.20   | Is this job _about_ what the CV is about?                | pgvector cosine                    |
| Experience | 0.20   | Does the candidate's experience fall in the job's range? | `min/max_experience_years`         |
| Location   | 0.15   | Is the job where the candidate wants to work?            | Normalized location vs preferences |
| Title      | 0.15   | Does the title match a target role?                      | Token overlap with `target_roles`  |

`weights_version = 1`. The five must sum to `1.0` or `Settings`
refuses to construct — weights summing to 0.95 would make every score
quietly 5% low while nothing looked broken, and the notification
threshold reads the absolute number.

**Changing any weight requires bumping `weights_version`**, otherwise
rows scored under two different models become indistinguishable.

## Abstain is not zero

Every signal returns `float | None`.

```text
None  =  "we could not look"     ->  ABSTAIN
0.0   =  "we looked, no match"   ->  a real score
```

This is the central design decision of the scoring model. A job with no
extractable skills would otherwise score 0.0 on 30% of the model
through no fault of its own, ranking below every job that merely
happened to have a parseable description — a data gap presented as a
fit gap, with nothing anywhere saying so.

The distinction survives into storage. The five signal columns on
`recommendations` are **nullable**, and NULL means abstain.
A test exists to keep them nullable. **Do not default them to 0.0.**

## The formula

```text
covered   = sum(weight for each signal whose value is not None)

weighted  = sum(weight * value for each non-abstaining signal) / covered
            (0.0 when covered == 0.0)

final     = weighted * quality_multiplier
```

`quality_multiplier` discounts the posting's own trustworthiness — a
staffing-agency listing, or one with no city. A multiplier, not a sixth
signal: a signal answers "does this fit", quality answers "should we
believe the posting at all".

`covered` is **stored on the row**, not discarded. A score built from
35% of the weight is not comparable with one built from 100%, and two
rows can carry the same `final_score` for entirely different reasons.
`weight_covered` is what tells them apart.

`covered == 0.0` is a real, expected state and gets its own reason as
the **first** entry in `match_reasons`, because a `final_score` of 0.0
otherwise has two indistinguishable causes: "everything scored zero"
and "nothing could be scored".

## Explanations

`match_reasons` lists what matched, then what abstained (prefixed
`abstained: `), then any quality reasons. Abstains are listed on
purpose — an explanation that names only what matched hides why the
score is as low as it is.

---

## The asymmetry, and why it has not been "fixed"

**Renormalisation means a job we could not assess can outrank a job we
assessed badly.**

An abstaining signal leaves the denominator. A signal scoring 0.0 stays
in it. So missing data is _mildly rewarded_ relative to bad data.

Pinned by `test_missing_data_can_outrank_bad_data` in
`tests/test_scoring_missing_data.py`.

### The closed form that matters

On the shape that dominates live data — skill and experience abstain,
location and title both 1.0 — the formula reduces exactly to:

```text
final = 0.4 * semantic + 0.6
```

Verified against stored values at difference 0.000e+00, and pinned by
`test_the_live_shape_has_a_floor_of_zero_point_six`.

Two consequences:

1. **`final_score` has a floor of 0.60 in that shape**, however poor
   the semantic match.
2. It clears the 0.7 notification threshold at `semantic >= 0.25`.

So the coverage floor and the score threshold **cannot be tuned
independently**. Lowering the floor admits precisely the population the
floor exists to guard against.

### Why the obvious fix is worse

The alternative — keep abstentions in the denominator — was measured by
`scripts/asymmetry_isolate.py` (read-only, self-checking against stored
`final_score` to 1.1e-16). The finding:

> Removing the asymmetry makes notification **strictly less
> reachable**. Abstentions kept in the denominator yield
> `notify_eligible = 0` at every coverage floor down to 0.30, because it
> halves the score range (max 0.9835 → 0.4917) against an unmoved 0.7
> threshold.

The asymmetry is currently the only reason any pair is near the gate.

### Status: measured, documented, **not decided**

This is abstention applied consistently, and it is a defensible
position. But nobody has _decided_ it, and Day 12 does not decide it
either — the Day 12 brief asks whether it is a bug or intentional, and
the honest answer is "intentional in mechanism, undecided in policy".

What Day 12 added is tests that state each property in words, so
whoever changes `combine()` has to change a test that says which
property they are giving up.

---

## The notification gate

The gate, not the score, is where a confident-looking number computed
from nothing gets stopped. Three conditions, **all** required, **all**
inclusive:

```python
final_score     >= notification_threshold          # default 0.7, per user
semantic_raw    >= semantic_notify_floor           # 0.62, absolute
weight_covered  >= min_weight_covered_to_notify    # 0.55
```

Every comparison is `>=`, so the boundary **qualifies**, and each is
tested at exactly its floor. Day 6's `median < 500` stayed silent when
the median was exactly 500; the boundary is the case that fails while
looking like it should pass.

| Gate           | Why it exists                                                                                                                                                 |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Score          | The obvious one. Is this a good match?                                                                                                                        |
| Semantic floor | Applied to **raw** similarity, never the rescaled score. Stops a day on which only irrelevant jobs were ingested from producing a confident-looking top match |
| Coverage       | Stops a data gap being read as a good match. Without it, a 0.7 threshold means something different for every job                                              |

### `notify_eligible = 0` on live data is the gate working

242 of 294 pairs sit at `weight_covered` **exactly 0.50**, five
hundredths under the floor. This is not a dial: `weight_covered` has
three observed values, so the floor is a step function. Coverage floors
of 0.45, 0.40 and 0.50 give the same result.

**Do not change the floor to make this go away.** The fix for low
coverage is a completed enrichment pass, which would give the skill
signal values and move coverage to 0.80.

---

## What the signals actually do

**Skill** — set overlap between the job's required skills and the
profile's, both as normalized catalog keys. Abstains when the job has
no extracted skills, which is currently most of them. The normalization
is why a job asking for `nodejs` matches a CV that wrote `Node.js`.

**Semantic** — cosine similarity from pgvector, clamped and rescaled.
The CV is embedded as a `RETRIEVAL_QUERY` and jobs as
`RETRIEVAL_DOCUMENT`; the same text under the two task types comes back
at cosine 0.861247, so the distinction is real. `semantic_raw` is stored
alongside the rescaled value because the gate reads the raw one.

**Experience** — the candidate's computed years against the job's
declared range. Abstains when the job declares no range, which on live
data is 98 of 99 jobs: Adzuna truncates descriptions at 500 characters,
and only 37 mention years at all.

**Location** — normalized comparison against preferred locations, with
country-only values treated separately.

**Title** — token overlap between the job title and the target roles.

---

## Known limits

- **Skill matching is exact set membership**, not semantic. "PyTorch"
  and "deep learning" are unrelated to this signal.
- **Company matching is exact, not substring** — deliberate. One
  company field holds three companies joined by `||`, and exact match
  will never catch it. Undecided.
- **`normalize_location` can take a locality instead of a city.**
  `app/services/locations.py` keeps only the first comma-separated
  segment of `jobs.location`, on the assumption that Adzuna's
  `display_name` is ordered most-specific-first (city, then state,
  then country). Job 88's location is `"Hussainialam, Hyderabad"` —
  Hussainialam is a locality _within_ Hyderabad, listed first — so it
  normalizes to `"hussainialam"` instead of `"hyderabad"`. Confirmed
  against live data 2026-09-05: `normalize_location("Hussainialam,
Hyderabad") == "hussainialam"`. It happened not to change that job's
  outcome (neither string matches a preference of "Delhi"), but it
  would silently miscount for a preference of "Hyderabad" specifically.
  **Not fixed.** "First segment is most specific" is a deliberate
  simplification (see the module docstring — this is a spelling/suffix
  problem, not a geography problem, on purpose), and changing it needs
  a decision about how to detect and strip a leading locality, not a
  patch.
- **`abstain_experience` at 98/99 is a source-data ceiling**, not an
  extraction bug.
- **The prediction on record**: after a full enrichment pass,
  `abstain_skill` should fall sharply and `abstain_experience` should
  fall only to roughly **60**. If it lands near 0, something is
  inventing values that were not in the description text. Still
  untested — Gemini quota.
- **No learning from feedback yet.** `user_feedback` accumulates and
  nothing reads it back into ranking. Deliberate for the MVP.
