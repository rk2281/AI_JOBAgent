"""Is the notify branch reachable, and if not, which gate is stopping it?

Day 8 ended with notify_eligible == 0 and the explanation that
min_weight_covered_to_notify (0.55) sits above the coverage any pair
currently achieves. That explanation is plausible and untested. Day 9's
graph gives decide_notification two outgoing edges, Day 10 schedules
it, and Day 11 hangs real Telegram messages off the edge that has never
executed -- so "which gate is actually binding" needs a number before
any of that is built, not after.

This script answers it WITHOUT re-scoring anything. Every input to the
three notification gates is already stored per row by run_scoring:
recommendations.final_score, .semantic_raw and .weight_covered. So the
counterfactual "how many pairs would qualify at a lower coverage floor"
is arithmetic over rows that already exist, not a second scoring run.

WHAT THIS DELIBERATELY DOES NOT DO

It does not override settings. is_notify_eligible() reads
settings.semantic_notify_floor and settings.min_weight_covered_to_notify
off the module-global singleton at call time, so the only way to make
that function evaluate a different floor is to mutate a global every
other importer shares. A probe that mutates process-wide config in
order to measure something is a probe that can change what it measures.

Instead the gates are evaluated locally, and that local evaluation is
CROSS-CHECKED against the real is_notify_eligible() at the real,
unmodified floor for every row. If the two ever disagree the script
says so and returns non-zero rather than printing a table that looks
fine. Same tactic scoring_isolate.py uses for cosine similarity: an
independent computation exists to prove the real one means what the
caller assumes, never to replace it.

WHY THE OVERLAP COLUMNS EXIST

"63 rows pass the score gate and 98 pass the semantic gate" says
nothing about how many pass both. Three independent pass counts can
each be large while the eligible count is zero. The grid therefore
reports the three-way intersection as its own column, and the binding
-gate analysis counts only rows that fail exactly ONE gate while
passing the other two -- the rows a single threshold change would
actually move.

No writes. No API calls. No settings mutation. Only reads.

    python -m scripts.notify_reachability_probe
    python -m scripts.notify_reachability_probe --user-id 2
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import func, select

from app.core.config import settings
from app.db.models.recommendation import Recommendation
from app.db.models.user import UserPreference
from app.db.session import dispose_engine, init_engine, session_scope
from app.services.job_scoring import is_notify_eligible

# The floors to evaluate. 0.55 is the current setting and stays first:
# it is the row the cross-check against the real is_notify_eligible()
# is performed on, and the only cell of the table that describes what
# the system does today rather than what it would do.
_COVERAGE_FLOORS = (0.55, 0.50, 0.45, 0.40)

# UserPreference.notification_threshold's own column default, and the
# value run_scoring falls back to for a user with no preference row.
# Duplicated from job_scoring._DEFAULT_NOTIFICATION_THRESHOLD rather
# than imported because that name is private; the script prints the
# thresholds it actually used, so a divergence is visible rather than
# silent.
_DEFAULT_NOTIFICATION_THRESHOLD = 0.7


def _fmt(value: float | None) -> str:
    """None prints as "--", never as 0."""
    return "--" if value is None else f"{value:.4f}"


def _threshold_for(row) -> float:
    """The per-user notification_threshold run_scoring would have used.

    A per-user setting, not a constant: run_scoring reads
    preferences.notification_threshold and only falls back to 0.7 when
    the user has no preference row at all. Assuming 0.7 for everyone
    would evaluate a gate some users are not actually subject to.
    """
    if row.notification_threshold is None:
        return _DEFAULT_NOTIFICATION_THRESHOLD
    return row.notification_threshold


def _is_complete(row) -> bool:
    """Whether all three gate inputs are present.

    A NULL cannot be compared with >= at all. run_scoring always stores
    1.0 - distance for semantic_raw so this should never exclude a row,
    but a silent exclusion is worse than a wrong number -- the caller
    counts and prints these separately.
    """
    return (
        row.final_score is not None
        and row.semantic_raw is not None
        and row.weight_covered is not None
    )


def _print_header(label: str) -> None:
    print(f"\n--- {label} ---")


async def run(user_id: int | None) -> int:
    async with session_scope() as session:
        statement = (
            select(
                Recommendation.user_id,
                Recommendation.job_id,
                Recommendation.final_score,
                Recommendation.semantic_raw,
                Recommendation.weight_covered,
                Recommendation.scoring_run_id,
                UserPreference.notification_threshold,
            )
            .outerjoin(UserPreference, UserPreference.user_id == Recommendation.user_id)
            .order_by(Recommendation.user_id, Recommendation.job_id)
        )
        if user_id is not None:
            statement = statement.where(Recommendation.user_id == user_id)

        rows = (await session.execute(statement)).all()

        total_in_table = (
            await session.execute(select(func.count()).select_from(Recommendation))
        ).scalar_one()

    if not rows:
        print("No recommendation rows. Nothing to probe.")
        return 1

    _print_header("Scope")
    print(f"  rows in recommendations : {total_in_table}")
    print(f"  rows in this probe      : {len(rows)}")
    run_ids = sorted({row.scoring_run_id for row in rows if row.scoring_run_id is not None})
    print(f"  distinct scoring_run_id : {run_ids or '--'}")
    print(f"  distinct user_id        : {sorted({row.user_id for row in rows})}")

    _print_header("Current thresholds (read, not modified)")
    print(f"  semantic_notify_floor        : {settings.semantic_notify_floor}")
    print(f"  min_weight_covered_to_notify : {settings.min_weight_covered_to_notify}")
    thresholds = sorted({_threshold_for(row) for row in rows})
    print(f"  notification_threshold       : {thresholds}")
    missing_prefs = sum(1 for row in rows if row.notification_threshold is None)
    print(f"  rows falling back to default : {missing_prefs} (default {_DEFAULT_NOTIFICATION_THRESHOLD})")

    incomplete = [row for row in rows if not _is_complete(row)]
    print(f"  NULL final_score             : {sum(1 for r in rows if r.final_score is None)}")
    print(f"  NULL semantic_raw            : {sum(1 for r in rows if r.semantic_raw is None)}")
    print(f"  NULL weight_covered          : {sum(1 for r in rows if r.weight_covered is None)}")
    print(f"  rows excluded from the grid  : {len(incomplete)}")

    scorable = [row for row in rows if _is_complete(row)]

    _print_header("weight_covered distribution")
    coverage_counts: dict[float, int] = {}
    for row in scorable:
        coverage_counts[row.weight_covered] = coverage_counts.get(row.weight_covered, 0) + 1
    for value in sorted(coverage_counts):
        print(f"  {_fmt(value)} : {coverage_counts[value]}")

    _print_header("final_score / semantic_raw range")
    finals = [row.final_score for row in scorable]
    semantics = [row.semantic_raw for row in scorable]
    if finals:
        print(f"  final_score  min/max : {_fmt(min(finals))} / {_fmt(max(finals))}")
    if semantics:
        print(f"  semantic_raw min/max : {_fmt(min(semantics))} / {_fmt(max(semantics))}")

    # --- cross-check the local gate evaluation against the real one ---
    #
    # Valid only at the real floor, because that is the only floor the
    # real function can be asked about without mutating settings.
    mismatches = 0
    for row in scorable:
        threshold = _threshold_for(row)
        real = is_notify_eligible(
            final_score=row.final_score,
            semantic_raw=row.semantic_raw,
            weight_covered=row.weight_covered,
            notification_threshold=threshold,
        )
        local = (
            row.final_score >= threshold
            and row.semantic_raw >= settings.semantic_notify_floor
            and row.weight_covered >= settings.min_weight_covered_to_notify
        )
        if real != local:
            mismatches += 1

    _print_header("Cross-check against the real is_notify_eligible()")
    print(f"  rows compared at the unmodified floor : {len(scorable)}")
    print(f"  disagreements                         : {mismatches}")
    if mismatches:
        print("  STOP: local gate evaluation does not match the real function.")
        print("  The grid below would be arithmetic about the wrong rule.")
        return 1
    print("  Local evaluation matches. The grid below is trustworthy.")

    _print_header("Grid: coverage floor vs gate passes")
    print("  floor  rows  pass_final  pass_sem  pass_cov  ALL_THREE   f&s   f&c   s&c")
    grid: dict[float, int] = {}
    for floor in _COVERAGE_FLOORS:
        pass_final = pass_sem = pass_cov = 0
        f_and_s = f_and_c = s_and_c = all_three = 0
        for row in scorable:
            threshold = _threshold_for(row)
            f = row.final_score >= threshold
            s = row.semantic_raw >= settings.semantic_notify_floor
            c = row.weight_covered >= floor
            pass_final += f
            pass_sem += s
            pass_cov += c
            f_and_s += f and s
            f_and_c += f and c
            s_and_c += s and c
            all_three += f and s and c
        grid[floor] = all_three
        marker = "  <- current" if floor == settings.min_weight_covered_to_notify else ""
        print(
            f"  {floor:.2f}  {len(scorable):4d}  {pass_final:10d}  {pass_sem:8d}  "
            f"{pass_cov:8d}  {all_three:9d}  {f_and_s:4d}  {f_and_c:4d}  {s_and_c:4d}{marker}"
        )

    _print_header("Binding gate: rows failing exactly one gate, passing the other two")
    print("  (the rows a single threshold change would actually move)")
    print("  floor  only_final_blocks  only_sem_blocks  only_cov_blocks  fails_2_or_3")
    for floor in _COVERAGE_FLOORS:
        only_f = only_s = only_c = fails_multi = 0
        for row in scorable:
            threshold = _threshold_for(row)
            f = row.final_score >= threshold
            s = row.semantic_raw >= settings.semantic_notify_floor
            c = row.weight_covered >= floor
            failed = (not f) + (not s) + (not c)
            if failed == 1:
                only_f += not f
                only_s += not s
                only_c += not c
            elif failed > 1:
                fails_multi += 1
        print(f"  {floor:.2f}  {only_f:17d}  {only_s:15d}  {only_c:15d}  {fails_multi:12d}")

    _print_header("Verdict")
    for floor in _COVERAGE_FLOORS:
        print(f"  coverage floor {floor:.2f} -> notify_eligible would be {grid[floor]}")

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Read-only notify-gate reachability probe.")
    parser.add_argument("--user-id", type=int, default=None)
    args = parser.parse_args()

    if init_engine() is None:
        print("DATABASE_URL is not configured.")
        raise SystemExit(1)

    # psycopg's async driver cannot use the ProactorEventLoop Windows
    # defaults to -- same preamble as scoring_isolate.py, and for the
    # same reason: this script always opens a database connection.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        exit_code = asyncio.run(run(args.user_id))
    finally:
        asyncio.run(dispose_engine())

    raise SystemExit(exit_code)
