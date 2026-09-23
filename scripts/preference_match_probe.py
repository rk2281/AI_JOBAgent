"""Day 16b Stage A -- read-only probe: which real jobs could reach user 13
through a preferences edit, hypothetically.

Answers one question without touching anything: if user 13's
target_roles / preferred_locations were set to match a specific real,
active, enriched job, would that job clear all three notification
gates? Nothing is written -- no preferences row, no recommendations
row, no scoring_runs row, no Telegram client, no Gemini client.

WHY THE REAL FUNCTIONS ARE IMPORTED, NOT REIMPLEMENTED

Unlike scripts/asymmetry_isolate.py -- which deliberately reimplements
combine()'s arithmetic so it cannot share code with the thing it
cross-checks -- this script calls score_title, score_location,
assess_quality, combine and is_notify_eligible() directly. There is no
"thing being cross-checked" here: this probe is not auditing the
scoring model, it is asking what the model would say under a
hypothetical input. Reimplementing that arithmetic by hand would be
exactly the "number computed by hand" the prompt forbids. Same
discipline for the free-text preferences fields: parse_list_input is
imported from app.services.onboarding, never re-typed, so the stored
form of a typed string here is guaranteed to match what a real
/preferences edit would store.

WHAT STAYS STORED, WHAT GETS RECOMPUTED

skill_score, experience_score and semantic_score/semantic_raw come
from the CV and the job -- a preferences edit cannot move them, so
they are read from `recommendations` unchanged and wrapped in
SignalScore objects (value only; the `reason` string is a stand-in,
since reason text does not feed the arithmetic combine() performs).
Only title and location are recomputed, using score_title/
score_location exactly as run_scoring calls them, against whichever
target_roles/preferred_locations the caller supplies (hypothetical or
current). assess_quality() is also called fresh (not trusted from the
stored quality_multiplier) since it takes only company/location, which
are job-owned and unaffected by any preference edit -- calling it
fresh is strictly more faithful and doubles as part of the self-check.

SEMANTIC_RAW NULL HANDLING MATCHES select_notifiable(), NOT AN
INVENTED DEFAULT

is_notify_eligible()'s semantic_raw parameter is typed as a required
float, never Optional -- run_scoring can never actually call it with
None, because raw_similarity = 1.0 - distance is always a real number
for every pair nearest_to() returns. So there is no run_scoring code
path this probe could "match" for a None semantic_raw; the honest
match is against the one place in this codebase that DOES have to
handle a None here: app.services.notification_delivery.
select_notifiable(), which passes float("-inf") for a None
semantic_raw (see its cross-check block) -- an explicit sentinel that
cannot pass the semantic gate at any floor, rather than a chosen
number like 0.0 that only happens to fail today because the floor is
positive. This probe does the same, via _semantic_raw_for_gate().

NULL IS ABSTAIN. Every signal value read from `recommendations` is
nullable and a NULL means abstain, not zero -- a CLAUDE.md section 1
row. Wrapped as SignalScore(value=None, ...) so combine() treats it
exactly as run_scoring did.

'SENT' is uppercase because that is the enum LABEL PostgreSQL stores
(SQLAlchemy persists an enum by NAME) -- CLAUDE.md section 1.

WHY select_notifiable()/evaluate_candidates() ARE NOT CALLED FOR THE
HYPOTHETICAL STATES

Both read RecommendationRepository's STORED rows and UserRepository's
STORED preferences straight from the database -- there is no parameter
on either function to hand them a hypothetical preferences row instead.
Pointing them at a hypothetical state would require actually writing
user_preferences (forbidden by this task's hard rules) or monkeypatching
around the real functions (which would defeat the entire point of using
them unmodified). So Stage A's whole-pool simulation below reimplements
only the SELECTION of "which stored rows would recompute as eligible" --
using the real score_title/score_location/combine/is_notify_eligible for
every number, never inventing an arithmetic shortcut -- and says plainly
that it cannot reproduce select_notifiable()'s window/cap truncation
(_GATE_WINDOW, max_notifications_per_user) without writing real data
first.

No writes. No API calls. No Telegram client. No Gemini client.

    python -m scripts.preference_match_probe
    python -m scripts.preference_match_probe --user-id 13
    python -m scripts.preference_match_probe --typed-role "..." --typed-location "..." --typed-location "..."
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import text

from app.core.config import settings
from app.db.session import dispose_engine, init_engine, session_scope
from app.services.job_scoring import is_notify_eligible
from app.services.locations import normalize_location
from app.services.onboarding import THRESHOLD_CHOICES, parse_list_input
from app.services.scoring import ScoredPair, assess_quality, combine
from app.services.scoring_signals import SignalScore, score_location, score_title

_TOLERANCE = 1e-9

# The four eligible-at-0.6 candidates found by the first Stage A run.
_REQUIRED_FULL_ROWS = (19, 92, 10, 99)


def _fmt(value: float | None, width: int = 7) -> str:
    return "--".rjust(width) if value is None else f"{value:.4f}".rjust(width)


def _signal_from_stored(value: float | None) -> SignalScore:
    return SignalScore(value, "stored")


def _semantic_raw_for_gate(value: float | None) -> float:
    """Match select_notifiable()'s cross-check exactly: a None semantic_raw
    becomes float("-inf"), an explicit sentinel that cannot pass the
    semantic gate at any floor -- not a chosen number like 0.0."""
    return value if value is not None else float("-inf")


async def _fetch_candidates(session, user_id: int) -> list:
    rows = (
        await session.execute(
            text(
                """
                SELECT
                    j.id AS job_id,
                    j.title AS job_title,
                    j.company AS job_company,
                    j.location AS job_location,
                    j.work_mode AS job_work_mode,
                    r.semantic_score,
                    r.semantic_raw,
                    r.skill_score,
                    r.experience_score,
                    r.location_score,
                    r.title_score,
                    r.weight_covered,
                    r.quality_multiplier,
                    r.final_score AS stored_final_score
                FROM jobs j
                JOIN recommendations r ON r.job_id = j.id AND r.user_id = :uid
                WHERE j.is_active = true
                  AND j.is_excluded = false
                  AND j.source <> 'synthetic_test'
                  AND j.skills_extracted_at IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM notifications n
                      WHERE n.user_id = :uid
                        AND n.job_id = j.id
                        AND n.status = 'SENT'
                  )
                ORDER BY j.id
                """
            ),
            {"uid": user_id},
        )
    ).all()
    return rows


async def _fetch_specific_candidates(session, user_id: int, job_ids: tuple[int, ...]) -> list:
    """Same shape/columns as _fetch_candidates, for specific job_ids,
    without the NOT-already-SENT filter -- used so item 2's required
    rows print even if one of them happens to already be SENT."""
    rows = (
        await session.execute(
            text(
                """
                SELECT
                    j.id AS job_id,
                    j.title AS job_title,
                    j.company AS job_company,
                    j.location AS job_location,
                    j.work_mode AS job_work_mode,
                    r.semantic_score,
                    r.semantic_raw,
                    r.skill_score,
                    r.experience_score,
                    r.location_score,
                    r.title_score,
                    r.weight_covered,
                    r.quality_multiplier,
                    r.final_score AS stored_final_score
                FROM jobs j
                JOIN recommendations r ON r.job_id = j.id AND r.user_id = :uid
                WHERE j.id = ANY(:job_ids)
                ORDER BY j.id
                """
            ),
            {"uid": user_id, "job_ids": list(job_ids)},
        )
    ).all()
    return rows


async def _count_sent_all(session, user_id: int) -> int:
    """The OLD number: every SENT row for this user, regardless of
    whether the job would even be a candidate under any other filter
    (includes synthetic_test jobs, inactive jobs, etc)."""
    result = await session.execute(
        text("SELECT count(*) FROM notifications WHERE user_id = :uid AND status = 'SENT'"),
        {"uid": user_id},
    )
    return int(result.scalar_one())


async def _count_sent_excluded_from_candidates(session, user_id: int) -> int:
    """The NEW, correct number: jobs that pass every OTHER candidate
    filter (active, not excluded, non-synthetic, skills_extracted_at
    not null, has a recommendations row for this user) and are excluded
    from the candidate list ONLY because a SENT row exists for them."""
    result = await session.execute(
        text(
            """
            SELECT count(*)
            FROM jobs j
            JOIN recommendations r ON r.job_id = j.id AND r.user_id = :uid
            WHERE j.is_active = true
              AND j.is_excluded = false
              AND j.source <> 'synthetic_test'
              AND j.skills_extracted_at IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM notifications n
                  WHERE n.user_id = :uid
                    AND n.job_id = j.id
                    AND n.status = 'SENT'
              )
            """
        ),
        {"uid": user_id},
    )
    return int(result.scalar_one())


async def _count_semantic_floor_candidates(session, user_id: int) -> int:
    """Cheap necessary-condition pre-check.

    semantic_raw is untouched by any preferences edit, so this single
    query predicts an upper bound on eligibility before the per-
    candidate recompute below runs: no candidate below the semantic
    floor can ever become eligible no matter what title/location do.
    """
    result = await session.execute(
        text(
            """
            SELECT count(*)
            FROM jobs j
            JOIN recommendations r ON r.job_id = j.id AND r.user_id = :uid
            WHERE j.is_active = true
              AND j.is_excluded = false
              AND j.source <> 'synthetic_test'
              AND j.skills_extracted_at IS NOT NULL
              AND r.semantic_raw >= :floor
            """
        ),
        {"uid": user_id, "floor": settings.semantic_notify_floor},
    )
    return int(result.scalar_one())


async def _current_preferences(session, user_id: int):
    result = await session.execute(
        text(
            "SELECT target_roles, preferred_locations, remote_only, notification_threshold "
            "FROM user_preferences WHERE user_id = :uid"
        ),
        {"uid": user_id},
    )
    return result.first()


def _recompute(
    row,
    *,
    target_roles: list[str],
    preferred_locations: list[str],
    remote_only: bool,
) -> ScoredPair:
    """The real combine(), fed stored semantic/skill/experience and
    freshly computed title/location/quality for the given (possibly
    hypothetical) preference inputs."""
    semantic = _signal_from_stored(row.semantic_score)
    skill = _signal_from_stored(row.skill_score)
    experience = _signal_from_stored(row.experience_score)

    title = score_title(row.job_title, target_roles)
    location = score_location(row.job_location, row.job_work_mode, preferred_locations, remote_only)
    quality = assess_quality(row.job_company, row.job_location)

    return combine(
        semantic=semantic,
        skill=skill,
        experience=experience,
        location=location,
        title=title,
        semantic_raw=row.semantic_raw,
        quality=quality,
    )


def _eligible_at_all_thresholds(scored: ScoredPair, row) -> dict[float, bool]:
    eligible_at = {}
    for threshold_value in sorted(set(THRESHOLD_CHOICES.values())):
        eligible_at[threshold_value] = is_notify_eligible(
            final_score=scored.final_score,
            semantic_raw=_semantic_raw_for_gate(row.semantic_raw),
            weight_covered=scored.weight_covered,
            notification_threshold=threshold_value,
        )
    return eligible_at


def _print_full_row(row, scored: ScoredPair, eligible_at: dict[float, bool]) -> None:
    title40 = (row.job_title or "")[:40].ljust(40)
    loc30 = (row.job_location or "")[:30].ljust(30)
    work_mode = (row.job_work_mode or "--").ljust(9)
    print(
        f"  {row.job_id:>6}  {title40}  {loc30}  {work_mode}  "
        f"{_fmt(row.semantic_raw)}  {_fmt(row.weight_covered)}  {_fmt(row.stored_final_score)}  "
        f"{_fmt(scored.title.value, 9)}  {_fmt(scored.location.value, 7)}  "
        f"{_fmt(scored.weight_covered, 7)}  "
        f"{_fmt(scored.final_score, 9)}  "
        f"{'Y' if eligible_at[0.6] else 'n':>3}  {'Y' if eligible_at[0.7] else 'n':>3}  "
        f"{'Y' if eligible_at[0.8] else 'n':>3}"
    )


_TABLE_HEADER = (
    "  job_id  title(40)                                 location(30)                    "
    "work_mode  sem_raw  cov(stored)  final(stored)  hyp_title  hyp_loc  hyp_cov  hyp_final  "
    "0.6  0.7  0.8"
)


async def run(user_id: int, typed_role: str | None, typed_locations: list[str]) -> int:
    async with session_scope() as session:
        prefs_row = await _current_preferences(session, user_id)
        if prefs_row is None:
            print(f"user {user_id} has no user_preferences row. Cannot self-check. Stopping.")
            return 1

        current_target_roles = list(prefs_row.target_roles or [])
        current_preferred_locations = list(prefs_row.preferred_locations or [])
        current_remote_only = bool(prefs_row.remote_only)
        current_threshold = float(prefs_row.notification_threshold)

        sent_all = await _count_sent_all(session, user_id)
        sent_excluded_from_candidates = await _count_sent_excluded_from_candidates(session, user_id)
        semantic_floor_candidates = await _count_semantic_floor_candidates(session, user_id)
        candidates = await _fetch_candidates(session, user_id)
        required_rows = await _fetch_specific_candidates(session, user_id, _REQUIRED_FULL_ROWS)

    print("--- Cheap pre-check (necessary condition only; semantic_raw is preference-invariant) ---")
    print(
        f"  candidates with stored semantic_raw >= semantic_notify_floor "
        f"({settings.semantic_notify_floor}): {semantic_floor_candidates}"
    )

    null_semantic_raw_count = sum(1 for row in candidates if row.semantic_raw is None)
    print("\n--- Item 5: semantic_raw NULL handling ---")
    print(
        "  is_notify_eligible()'s semantic_raw parameter is a required float; run_scoring can "
        "never pass None (raw_similarity = 1.0 - distance is always real). The one real function "
        "that DOES handle a None semantic_raw is select_notifiable()'s cross-check, which passes "
        "float('-inf') -- an explicit sentinel that cannot pass the gate at any floor. This probe "
        "now matches that exactly (previously it passed 0.0, an invented default)."
    )
    print(f"  candidates with semantic_raw IS NULL: {null_semantic_raw_count}")
    if null_semantic_raw_count == 0:
        print(
            "  None in this candidate set -- the fix changes no printed number here, but it is "
            "now correct for any row that does have one."
        )
    else:
        print(
            f"  {null_semantic_raw_count} row(s) affected. With semantic_notify_floor="
            f"{settings.semantic_notify_floor} > 0.0, both 0.0 and float('-inf') fail the gate "
            "for these rows -- same boolean outcome today, but -inf is what the real code does, "
            "not a coincidence-dependent stand-in."
        )

    print("\n--- Item 1: 'already SENT' count, old vs corrected ---")
    print(f"  OLD (every SENT row for user {user_id}, any job, any filter state): {sent_all}")
    print(
        f"  NEW (candidate-filter jobs excluded ONLY because a SENT row exists): "
        f"{sent_excluded_from_candidates}"
    )

    if not candidates:
        print("\nNo candidates at all (active, not excluded, real source, enriched, not already SENT).")
        return 0

    print(f"\n--- Current preferences (user {user_id}) ---")
    print(f"  target_roles          : {current_target_roles}")
    print(f"  preferred_locations   : {current_preferred_locations}")
    print(f"  remote_only           : {current_remote_only}")
    print(f"  notification_threshold: {current_threshold}")

    # --- self-check: 3 arbitrary rows + the 4 required rows ----------------
    self_check_rows = list(candidates[:3])
    seen_ids = {r.job_id for r in self_check_rows}
    for r in required_rows:
        if r.job_id not in seen_ids:
            self_check_rows.append(r)
            seen_ids.add(r.job_id)

    print(
        f"\n--- Self-check: reconstruction reproduces stored final_score, "
        f"CURRENT preferences ({len(self_check_rows)} rows) ---"
    )
    mismatches = 0
    worst = 0.0
    for row in self_check_rows:
        scored = _recompute(
            row,
            target_roles=current_target_roles,
            preferred_locations=current_preferred_locations,
            remote_only=current_remote_only,
        )
        recomputed_final = scored.final_score
        delta = abs(recomputed_final - row.stored_final_score)
        worst = max(worst, delta)
        status = "OK" if delta <= _TOLERANCE else "MISMATCH"
        if delta > _TOLERANCE:
            mismatches += 1
        print(
            f"  job {row.job_id:>5}  stored={row.stored_final_score:.9f}  "
            f"recomputed={recomputed_final:.9f}  delta={delta:.3e}  {status}"
        )
    print(f"  worst delta: {worst:.3e}  tolerance: {_TOLERANCE:.0e}  mismatches: {mismatches}")

    if mismatches:
        print("\n  STOP: the reconstruction does not reproduce stored final_score.")
        print("  The probe is wrong, not the data. No hypothetical numbers below can be trusted.")
        return 1

    print("  Reproduced. Hypothetical numbers below are computed on the same, validated basis.")

    # --- hypothetical recompute for every candidate (job.title / job.location target) ---
    results = []
    title_abstains: list[int] = []
    location_locality_notes: list[tuple[int, str, str]] = []

    for row in candidates:
        hypothetical_target_roles = [row.job_title]
        hypothetical_location_key = normalize_location(row.job_location)
        hypothetical_preferred_locations = [hypothetical_location_key] if hypothetical_location_key else []

        scored = _recompute(
            row,
            target_roles=hypothetical_target_roles,
            preferred_locations=hypothetical_preferred_locations,
            remote_only=current_remote_only,
        )

        if scored.title.value is None:
            title_abstains.append(row.job_id)

        if row.job_location and hypothetical_location_key:
            typed_city = hypothetical_location_key
            if "," in (row.job_location or "") or hypothetical_location_key != (row.job_location or "").strip().lower():
                location_locality_notes.append((row.job_id, row.job_location, typed_city))

        eligible_at = _eligible_at_all_thresholds(scored, row)

        results.append(
            {
                "row": row,
                "scored": scored,
                "eligible_at": eligible_at,
            }
        )

    results.sort(key=lambda r: r["scored"].final_score, reverse=True)

    print(f"\n--- Top 15 of {len(results)} candidates, sorted by hypothetical final_score descending ---")
    print(_TABLE_HEADER)
    for r in results[:15]:
        _print_full_row(r["row"], r["scored"], r["eligible_at"])

    eligible_06 = sum(1 for r in results if r["eligible_at"][0.6])
    eligible_06_rows = [r for r in results if r["eligible_at"][0.6]]

    print("\n--- Counts ---")
    print(f"  candidates considered            : {len(results)}")
    print(f"  candidates eligible at 0.6        : {eligible_06}")
    if eligible_06_rows:
        print("  eligible job_ids (0.6)           :", [r["row"].job_id for r in eligible_06_rows])

    # --- item 2: full row for each required job_id, whether or not in top 15 ---
    print(f"\n--- Item 2: full row for required job_ids {_REQUIRED_FULL_ROWS} ---")
    print(_TABLE_HEADER)
    results_by_id = {r["row"].job_id: r for r in results}
    for job_id in _REQUIRED_FULL_ROWS:
        r = results_by_id.get(job_id)
        if r is None:
            print(f"  job {job_id}: not in the candidate pool (check filters / already SENT).")
            continue
        _print_full_row(r["row"], r["scored"], r["eligible_at"])

    print("\n--- Findings to state, not fix ---")
    if title_abstains:
        print(
            f"  title abstains for {len(title_abstains)} job(s) (weak-token title against itself): "
            f"{title_abstains[:20]}{' ...' if len(title_abstains) > 20 else ''}"
        )
    else:
        print("  title never abstains against its own job title for any candidate.")

    if location_locality_notes:
        print(
            f"  normalize_location took only the first comma segment for {len(location_locality_notes)} job(s); "
            "the string you would have to type is the SECOND column below, not the raw job.location:"
        )
        for job_id, raw_loc, typed in location_locality_notes[:20]:
            print(f"    job {job_id}: raw='{raw_loc}' -> type='{typed}'")
        if len(location_locality_notes) > 20:
            print(f"    ... and {len(location_locality_notes) - 20} more")
    else:
        print("  no candidate's location differs from its normalized form (no locality-vs-city gap observed here).")

    if eligible_06 == 0:
        print("\n  NO CANDIDATE IS ELIGIBLE AT 0.6.")

    # --- item 4: real handler parsing on typed values -----------------------
    if typed_role is not None or typed_locations:
        print("\n--- Item 4: typed-value test through the REAL parse_list_input() ---")
        job19_row = next((c for c in candidates if c.job_id == 19), None)
        if job19_row is None:
            job19_row = next((r for r in required_rows if r.job_id == 19), None)

        if typed_role is not None:
            stored_roles = parse_list_input(typed_role)
            print(f"  typed role text : {typed_role!r}")
            print(f"  stored form     : {stored_roles}")
            if job19_row is not None:
                title_signal = score_title(job19_row.job_title, stored_roles)
                print(f"  job 19 title_score under this role list: {_fmt(title_signal.value)} ({title_signal.reason})")

        for typed_location in typed_locations:
            stored_locations = parse_list_input(typed_location)
            print(f"  typed location text : {typed_location!r}")
            print(f"  stored form         : {stored_locations}")
            if job19_row is not None:
                location_signal = score_location(
                    job19_row.job_location, job19_row.job_work_mode, stored_locations, current_remote_only
                )
                print(
                    f"  job 19 location_score under this location list: "
                    f"{_fmt(location_signal.value)} ({location_signal.reason})"
                )

    # --- item 6: whole-pool simulation --------------------------------------
    if typed_role is not None and typed_locations:
        typed_location = typed_locations[0]
        stored_roles = parse_list_input(typed_role)
        stored_locations = parse_list_input(typed_location)

        print("\n--- Item 6: whole-pool simulation for Stage B ---")
        print(f"  typed role -> stored      : {stored_roles}")
        print(f"  typed location -> stored  : {stored_locations}")
        print(f"  evaluated at user {user_id}'s CURRENT notification_threshold: {current_threshold}")

        states = {
            "state after edit1=ROLES only (locations still CURRENT)": (
                stored_roles,
                current_preferred_locations,
            ),
            "state after edit1=LOCATIONS only (roles still CURRENT)": (
                current_target_roles,
                stored_locations,
            ),
            "state after BOTH edits (order-independent final state)": (
                stored_roles,
                stored_locations,
            ),
        }

        for label, (roles, locations) in states.items():
            print(f"\n  -- {label} --")
            print(f"     target_roles={roles}  preferred_locations={locations}")
            eligible_here = []
            for row in candidates:
                scored = _recompute(
                    row, target_roles=roles, preferred_locations=locations, remote_only=current_remote_only
                )
                eligible = is_notify_eligible(
                    final_score=scored.final_score,
                    semantic_raw=_semantic_raw_for_gate(row.semantic_raw),
                    weight_covered=scored.weight_covered,
                    notification_threshold=current_threshold,
                )
                if eligible:
                    eligible_here.append((row.job_id, scored.final_score, scored.weight_covered, row.semantic_raw))
            eligible_here.sort(key=lambda t: t[1], reverse=True)
            if eligible_here:
                print(f"     eligible at {current_threshold} ({len(eligible_here)}):")
                for job_id, final_score, weight_covered, semantic_raw in eligible_here:
                    print(
                        f"       job {job_id:>5}  final={final_score:.4f}  "
                        f"cov={weight_covered:.4f}  sem_raw={_fmt(semantic_raw).strip()}"
                    )
            else:
                print(f"     eligible at {current_threshold}: none")

        print(
            "\n  select_notifiable()/evaluate_candidates() were NOT called for these hypothetical "
            "states: both read RecommendationRepository's STORED rows and UserRepository's STORED "
            "preferences directly from the database, with no parameter to substitute a hypothetical "
            "preferences row. Reaching them would require writing user_preferences first, which this "
            "task's hard rules forbid. The lists above are this script's own selection over the SAME "
            "real score_title/score_location/combine/is_notify_eligible functions, but they do NOT "
            "reproduce _GATE_WINDOW (top 25 by final_score) or the max_notifications_per_user send cap "
            "(3) -- if more than 3 jobs are eligible in a state above, real delivery would send at most "
            "3 of them, chosen by descending final_score within the top 25, and this script does not "
            "claim to know which 3 without those real functions running against real stored data."
        )

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Day 16b Stage A -- read-only preference-match probe.")
    parser.add_argument("--user-id", type=int, default=13)
    parser.add_argument("--typed-role", type=str, default=None)
    parser.add_argument("--typed-location", type=str, action="append", default=[])
    args = parser.parse_args()

    if init_engine() is None:
        print("DATABASE_URL is not configured.")
        raise SystemExit(1)

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        exit_code = asyncio.run(run(args.user_id, args.typed_role, args.typed_location))
    finally:
        asyncio.run(dispose_engine())

    raise SystemExit(exit_code)
