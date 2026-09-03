"""Where the graph goes next, as plain functions over the state.

No LangGraph import, no database, no service call. Each of these takes
the state dict and returns a string.

The reason they are here rather than inline in graph.py is the same
reason is_notify_eligible() and select_status() were pulled out of
run_scoring on Day 8: a routing rule you cannot test at its boundary
without standing up a database gets tested NEAR its boundary instead of
AT it, and the boundary is the case that fails while looking like it
should pass.

Each router also publishes the set of values it can return. graph.py
builds its path maps from those sets, and a test asserts the two agree
-- which is what catches a typo'd edge target. LangGraph reports that
only at run time, on the branch that is never taken, which for this
graph is the notification branch nobody has executed yet.
"""

from __future__ import annotations

from typing import Any

# Scoring statuses that end the run before a notification decision is
# meaningful. Both mean "there was nothing to decide about", and neither
# is a failure: NO_CANDIDATE_JOBS is an empty jobs table, and
# NO_SCORABLE_USERS is nobody with an embedded CV.
SCORING_TERMINAL_STATUSES = frozenset({"no_candidate_jobs", "no_scorable_users"})

ROUTE_AFTER_TARGETS = frozenset({"discover_jobs", "finalise"})
ROUTE_AFTER_SCORING = frozenset({"decide_notification", "finalise"})
ROUTE_NOTIFICATION = frozenset({"notify", "no_qualifying"})


def route_after_targets(state: dict[str, Any]) -> str:
    """Continue only if at least one user could actually be scored.

    The boundary is exactly one. A run for a single user with an
    embedded CV is a real run; the ingestion and enrichment passes
    below it are worth their quota. Zero is the only count that makes
    the rest of the graph pointless, and it stops here rather than
    after spending an Adzuna pass and a day of Gemini calls on a run
    that would score nobody.

    Reads users_with_embedded_cv rather than users_with_profile,
    because having a profile and being scorable are different
    questions -- run_scoring skips a profile whose active CV version
    has no embedding, and counts it in users_skipped_no_cv.
    """
    targets = state.get("targets") or {}
    if int(targets.get("users_with_embedded_cv") or 0) >= 1:
        return "discover_jobs"
    return "finalise"


def route_after_scoring(state: dict[str, Any]) -> str:
    """Decide notification unless scoring had nothing to decide about.

    Note what does NOT stop here. ALL_ABSTAINED and DEGENERATE both
    describe a ranking that carries no information, and both still
    reach decide_notification -- because both wrote recommendation
    rows, and a run that wrote rows has made a notification decision
    whether or not the decision is interesting. They are surfaced as
    `degraded` in the summary instead. Routing them to finalise would
    hide a bad ranking behind the same path as an empty table.
    """
    scoring = state.get("scoring") or {}
    if scoring.get("status") in SCORING_TERMINAL_STATUSES:
        return "finalise"
    if not scoring:
        return "finalise"
    return "decide_notification"


def route_notification(state: dict[str, Any]) -> str:
    """The branch Day 11 will hang real Telegram messages off.

    Both values currently lead to finalise. That is deliberate and it
    is not a stub: the rule is real, it is exercised in both directions
    by tests, and Day 11 changes one entry of graph.py's path map
    rather than inventing a branch that has never run. The first time
    the notify edge executes for real it will be sending a message to a
    person, so it is proven reachable now, with a stubbed scoring
    result, rather than discovered then.

    notify_eligible is a count that run_scoring already computed with
    is_notify_eligible()'s three `>=` gates. One eligible pair is a
    notification; this does not re-apply any threshold of its own,
    because a second copy of that rule is a second thing to keep in
    step.
    """
    if int(state.get("notify_eligible") or 0) >= 1:
        return "notify"
    return "no_qualifying"
