"""Separate the causes behind notify_eligible = 0, without deciding anything.

Day 11 attaches Telegram delivery to a branch that has never fired on
real data. `notify_eligible` is 0 because `weight_covered` sits at 0.50
against a floor of 0.55, and the Day 9 probe established that the floor
is a step function with three observed values, so lowering it to 0.45 or
0.40 changes nothing.

Design Note section 10 deliberately did not decide the abstention
asymmetry -- an abstaining signal leaves the denominator while a 0.0
stays in it, so missing data can outrank bad data -- on the grounds that
deciding it under time pressure is how it gets patched instead of
decided. That reasoning still holds. **This script changes nothing and
recommends nothing.** It exists so the decision arrives at Day 11 with
evidence attached rather than as a guess made in a hurry, and a script
that argues for an answer is worth less than one that shows the
alternatives.

WHY combine() IS NOT IMPORTED

Every number below is computed here, from the columns `recommendations`
already stores. Importing `combine()` and mutating its behaviour to try
each candidate would make this script share code with the thing it is
cross-checking, which is precisely what
test_scorable_targets_check_does_not_reference_the_shared_predicate
exists to prevent -- a candidate evaluated by the code that produced the
problem cannot disagree with it.

The arithmetic is reimplemented instead, and that reimplementation is
SELF-CHECKING: candidate A reproduces the stored `final_score` from the
stored signal columns. If A does not match to within floating-point
tolerance, the formula here is wrong and every other candidate is
worthless -- so the script says so and stops rather than printing a
table nobody should trust. Same tactic as scoring_isolate.py's
independently computed cosine similarity: verification math, not a
second implementation.

THE FORMULA, read off app/services/scoring.py rather than imported

    weight_covered = sum of weights whose signal is NOT NULL
    weighted_total = sum(weight * value) / weight_covered
    final_score    = weighted_total * quality_multiplier

NULL IS ABSTAIN. The five signal columns on `recommendations` are
nullable and a NULL means the signal declined to score, not that it
scored zero -- a CLAUDE.md section 1 row. Every count below reads them
that way.

No writes. No API calls. Only reads.

    python -m scripts.asymmetry_isolate
    python -m scripts.asymmetry_isolate --run-id 4
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter

from sqlalchemy import text

from app.core.config import settings
from app.db.session import dispose_engine, init_engine, session_scope

# The five weights, read from Settings rather than typed in. A validator
# in config.py refuses to construct Settings if they do not sum to 1.0,
# so this is the one place the numbers cannot silently disagree with the
# code that produced the rows.
_SIGNALS = ("semantic", "skill", "experience", "location", "title")

_FLOORS = (0.55, 0.50, 0.45, 0.40, 0.35, 0.30)

_TOLERANCE = 1e-6


def _weights() -> dict[str, float]:
    return {
        "semantic": settings.weight_semantic,
        "skill": settings.weight_skill,
        "experience": settings.weight_experience,
        "location": settings.weight_location,
        "title": settings.weight_title,
    }


def _header(label: str) -> None:
    print(f"\n--- {label} ---")


def _fmt(value: float | None) -> str:
    """None prints as '--', never as 0. The distinction is the subject."""
    return "--" if value is None else f"{value:.4f}"


def _recompute(row, weights: dict[str, float]) -> tuple[float, float, float]:
    """(weight_covered, final_as_is, final_abstain_in_denominator).

    Candidate A renormalises by the covered weight, which is what the
    code does today. Candidate B leaves every abstaining signal in the
    denominator at 0.0, which is the alternative the asymmetry note
    describes. Both are computed from the same stored signals so the
    only difference between them is the denominator.
    """
    covered = 0.0
    raw_total = 0.0
    for name in _SIGNALS:
        value = getattr(row, f"{name}_score")
        if value is None:
            continue
        covered += weights[name]
        raw_total += weights[name] * value

    quality = row.quality_multiplier

    final_as_is = (raw_total / covered) * quality if covered > 0 else 0.0
    # Denominator 1.0: every weight counted, abstentions contributing 0.
    final_kept = raw_total * quality

    return covered, final_as_is, final_kept


async def run(run_id: int | None) -> int:
    weights = _weights()

    async with session_scope() as session:
        where = "WHERE scoring_run_id = :rid" if run_id is not None else ""
        rows = (
            await session.execute(
                text(
                    "SELECT user_id, job_id, scoring_run_id, final_score, semantic_raw, "
                    "weight_covered, quality_multiplier, semantic_score, skill_score, "
                    "experience_score, location_score, title_score "
                    f"FROM recommendations {where} ORDER BY user_id, job_id"
                ),
                {"rid": run_id} if run_id is not None else {},
            )
        ).all()

    if not rows:
        print("No recommendation rows. Nothing to separate.")
        return 1

    _header("Scope")
    print(f"  pairs                  : {len(rows)}")
    print(f"  distinct scoring_run_id: {sorted({r.scoring_run_id for r in rows})}")
    print(f"  distinct user_id       : {sorted({r.user_id for r in rows})}")
    print(f"  distinct job_id        : {len({r.job_id for r in rows})}")

    _header("Weights (from Settings, not typed in)")
    for name, weight in weights.items():
        print(f"  {name:11} {weight}")
    print(f"  sum         {sum(weights.values())}")

    _header("Gates in force")
    print(f"  notification_threshold       : 0.7 (per-user; all users default)")
    print(f"  semantic_notify_floor        : {settings.semantic_notify_floor}")
    print(f"  min_weight_covered_to_notify : {settings.min_weight_covered_to_notify}")

    # --- self-check before any candidate is trusted -----------------------
    mismatches = 0
    worst = 0.0
    for row in rows:
        _, final_as_is, _ = _recompute(row, weights)
        delta = abs(final_as_is - row.final_score)
        worst = max(worst, delta)
        if delta > _TOLERANCE:
            mismatches += 1

    _header("Self-check: does candidate A reproduce the stored final_score?")
    print(f"  rows compared      : {len(rows)}")
    print(f"  mismatches         : {mismatches}")
    print(f"  worst difference   : {worst:.3e}")
    if mismatches:
        print("  STOP: the formula here does not reproduce what the code stored.")
        print("  Every candidate below would be arithmetic about the wrong rule.")
        return 1
    print("  Reproduced. The candidates below are computed on the same basis.")

    # --- weight_covered distribution --------------------------------------
    _header("weight_covered distribution (as stored)")
    for value, count in sorted(Counter(r.weight_covered for r in rows).items()):
        print(f"  {_fmt(value)} : {count}")

    # --- who abstains, how often, over how many jobs -----------------------
    _header("Abstentions per signal (NULL is abstain)")
    print("  signal       weight   pairs_abstained   distinct_jobs   pct_of_pairs")
    for name in _SIGNALS:
        abstained = [r for r in rows if getattr(r, f"{name}_score") is None]
        jobs = {r.job_id for r in abstained}
        pct = 100.0 * len(abstained) / len(rows)
        print(
            f"  {name:11}  {weights[name]:.2f}   {len(abstained):15d}   "
            f"{len(jobs):13d}   {pct:11.1f}%"
        )

    # --- the candidates ----------------------------------------------------
    #
    # Each row is evaluated against all three gates, not just the coverage
    # one. Three independent pass counts can each be large while the
    # eligible count is zero, which is what the Day 9 probe found.
    _header("Candidate treatments: how many pairs clear ALL THREE gates")
    print("  Gates: final_score >= 0.7, semantic_raw >= 0.62, weight_covered >= floor")
    print()
    print("  candidate                        floor   pass_final  pass_sem  pass_cov  ELIGIBLE")

    results: dict[str, int] = {}

    for floor in _FLOORS:
        pass_final = pass_sem = pass_cov = eligible = 0
        for row in rows:
            covered, final_as_is, _ = _recompute(row, weights)
            f = final_as_is >= 0.7
            s = (row.semantic_raw or 0.0) >= settings.semantic_notify_floor
            c = covered >= floor
            pass_final += f
            pass_sem += s
            pass_cov += c
            eligible += f and s and c
        marker = "  <- current" if floor == settings.min_weight_covered_to_notify else ""
        label = "A as-is (renormalised)"
        print(
            f"  {label:32} {floor:.2f}   {pass_final:10d}  {pass_sem:8d}  "
            f"{pass_cov:8d}  {eligible:8d}{marker}"
        )
        results[f"A@{floor:.2f}"] = eligible

    print()
    for floor in _FLOORS:
        pass_final = pass_sem = pass_cov = eligible = 0
        for row in rows:
            _, _, final_kept = _recompute(row, weights)
            # Under candidate B every weight is in the denominator, so
            # coverage is 1.0 by construction and the coverage gate can
            # never bind. That is the point of showing it: it moves the
            # problem entirely onto final_score.
            covered_b = 1.0
            f = final_kept >= 0.7
            s = (row.semantic_raw or 0.0) >= settings.semantic_notify_floor
            c = covered_b >= floor
            pass_final += f
            pass_sem += s
            pass_cov += c
            eligible += f and s and c
        label = "B abstain kept in denominator"
        print(
            f"  {label:32} {floor:.2f}   {pass_final:10d}  {pass_sem:8d}  "
            f"{pass_cov:8d}  {eligible:8d}"
        )
        results[f"B@{floor:.2f}"] = eligible

    # --- what binds, once coverage stops --------------------------------
    _header("Rows failing exactly one gate, passing the other two (candidate A)")
    print("  floor  only_final  only_semantic  only_coverage  fails_2_or_3")
    for floor in _FLOORS:
        only_f = only_s = only_c = multi = 0
        for row in rows:
            covered, final_as_is, _ = _recompute(row, weights)
            f = final_as_is >= 0.7
            s = (row.semantic_raw or 0.0) >= settings.semantic_notify_floor
            c = covered >= floor
            failed = (not f) + (not s) + (not c)
            if failed == 1:
                only_f += not f
                only_s += not s
                only_c += not c
            elif failed > 1:
                multi += 1
        print(f"  {floor:.2f}  {only_f:10d}  {only_s:13d}  {only_c:13d}  {multi:12d}")

    _header("Score ranges under each candidate")
    finals_a = [_recompute(r, weights)[1] for r in rows]
    finals_b = [_recompute(r, weights)[2] for r in rows]
    semantics = [r.semantic_raw for r in rows if r.semantic_raw is not None]
    print(f"  A final_score  min/max : {_fmt(min(finals_a))} / {_fmt(max(finals_a))}")
    print(f"  B final_score  min/max : {_fmt(min(finals_b))} / {_fmt(max(finals_b))}")
    if semantics:
        print(f"  semantic_raw   min/max : {_fmt(min(semantics))} / {_fmt(max(semantics))}")

    _header("Verdict (counts only -- no recommendation)")
    nonzero = {name: count for name, count in results.items() if count}
    for name in sorted(results):
        print(f"  {name:10} -> notify_eligible {results[name]}")
    print()
    if nonzero:
        print(f"  Candidates producing a NON-ZERO notify_eligible: {sorted(nonzero)}")
    else:
        print("  NO CANDIDATE PRODUCES A NON-ZERO notify_eligible.")
        print("  Neither treatment of abstention, at any coverage floor down to")
        print("  0.30, puts a single pair through all three gates on this data.")

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Read-only abstention-asymmetry isolate.")
    parser.add_argument("--run-id", type=int, default=None)
    args = parser.parse_args()

    if init_engine() is None:
        print("DATABASE_URL is not configured.")
        raise SystemExit(1)

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        exit_code = asyncio.run(run(args.run_id))
    finally:
        asyncio.run(dispose_engine())

    raise SystemExit(exit_code)
