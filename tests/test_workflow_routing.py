"""Every branch of the graph, tested at its exact boundary.

No database, no LangGraph, no event loop.

The parametrised test over ScoringStatus is the one that matters most:
it walks every member of the enum, so a status added later cannot
quietly fall through a router's default. Day 8's do-not-fix list notes
that PARTIAL and FAILED are never set by anything today -- they are
covered here anyway, because "nothing sets it yet" is not "nothing will
route on it".
"""

from __future__ import annotations

import pytest

from app.db.models.scoring import ScoringStatus
from app.workflows.routing import (
    ROUTE_AFTER_SCORING,
    ROUTE_AFTER_TARGETS,
    ROUTE_NOTIFICATION,
    SCORING_TERMINAL_STATUSES,
    route_after_scoring,
    route_after_targets,
    route_notification,
)


# --- after target resolution ---------------------------------------------


def test_no_scorable_users_stops_before_spending_any_quota() -> None:
    state = {"targets": {"users_with_profile": 3, "users_with_embedded_cv": 0}}
    assert route_after_targets(state) == "finalise"


def test_exactly_one_scorable_user_is_enough_to_continue() -> None:
    """The boundary. One user with an embedded CV is a real run."""
    state = {"targets": {"users_with_profile": 1, "users_with_embedded_cv": 1}}
    assert route_after_targets(state) == "discover_jobs"


def test_three_scorable_users_continue() -> None:
    state = {"targets": {"users_with_profile": 3, "users_with_embedded_cv": 3}}
    assert route_after_targets(state) == "discover_jobs"


def test_profiles_without_embedded_cvs_do_not_count_as_scorable() -> None:
    """Having a profile and being scorable are different questions."""
    state = {"targets": {"users_with_profile": 5, "users_with_embedded_cv": 0}}
    assert route_after_targets(state) == "finalise"


def test_a_missing_targets_block_stops_rather_than_crashing() -> None:
    assert route_after_targets({}) == "finalise"


# --- after scoring -------------------------------------------------------


@pytest.mark.parametrize("status", list(ScoringStatus))
def test_every_scoring_status_routes_to_a_declared_node(status: ScoringStatus) -> None:
    """No member of the enum may fall through to an undeclared target."""
    state = {"scoring": {"status": status.value}}
    assert route_after_scoring(state) in ROUTE_AFTER_SCORING


@pytest.mark.parametrize("status", sorted(SCORING_TERMINAL_STATUSES))
def test_scoring_statuses_that_end_the_run(status: str) -> None:
    assert route_after_scoring({"scoring": {"status": status}}) == "finalise"


def test_a_degenerate_ranking_still_reaches_the_notification_decision() -> None:
    """It wrote rows, so a decision was made. Routing it to finalise
    would hide a meaningless ranking behind the same path as an empty
    jobs table."""
    state = {"scoring": {"status": ScoringStatus.DEGENERATE.value}}
    assert route_after_scoring(state) == "decide_notification"


def test_an_all_abstained_run_still_reaches_the_notification_decision() -> None:
    state = {"scoring": {"status": ScoringStatus.ALL_ABSTAINED.value}}
    assert route_after_scoring(state) == "decide_notification"


def test_the_healthy_quiet_day_reaches_the_notification_decision() -> None:
    state = {"scoring": {"status": ScoringStatus.COMPLETE_NO_QUALIFYING.value}}
    assert route_after_scoring(state) == "decide_notification"


def test_a_dry_run_reaches_the_notification_decision() -> None:
    """run_scoring returns status "dry_run", which is not a
    ScoringStatus member at all."""
    assert route_after_scoring({"scoring": {"status": "dry_run"}}) == "decide_notification"


def test_a_missing_scoring_block_stops_rather_than_crashing() -> None:
    assert route_after_scoring({}) == "finalise"


# --- the notification branch ---------------------------------------------


def test_zero_eligible_takes_the_quiet_branch() -> None:
    assert route_notification({"notify_eligible": 0}) == "no_qualifying"


def test_exactly_one_eligible_takes_the_notify_branch() -> None:
    """The boundary, and the branch Day 11 sends real messages from.

    Proven reachable here, with a number, rather than discovered the
    first time it runs against a person's Telegram account.
    """
    assert route_notification({"notify_eligible": 1}) == "notify"


def test_several_eligible_take_the_notify_branch() -> None:
    assert route_notification({"notify_eligible": 3}) == "notify"


def test_a_missing_notify_eligible_takes_the_quiet_branch() -> None:
    """None is not zero, but it is certainly not a notification."""
    assert route_notification({}) == "no_qualifying"
    assert route_notification({"notify_eligible": None}) == "no_qualifying"


# --- every router's outputs are declared ---------------------------------


def test_route_after_targets_only_returns_declared_values() -> None:
    cases = [
        {},
        {"targets": {}},
        {"targets": {"users_with_embedded_cv": 0}},
        {"targets": {"users_with_embedded_cv": 1}},
        {"targets": {"users_with_embedded_cv": 99}},
    ]
    assert {route_after_targets(state) for state in cases} <= ROUTE_AFTER_TARGETS


def test_route_after_scoring_only_returns_declared_values() -> None:
    cases = [{}, {"scoring": {}}, {"scoring": {"status": "dry_run"}}] + [
        {"scoring": {"status": status.value}} for status in ScoringStatus
    ]
    assert {route_after_scoring(state) for state in cases} <= ROUTE_AFTER_SCORING


def test_route_notification_only_returns_declared_values() -> None:
    cases = [{}, {"notify_eligible": None}, {"notify_eligible": 0}, {"notify_eligible": 7}]
    assert {route_notification(state) for state in cases} <= ROUTE_NOTIFICATION


def test_both_notification_branches_are_actually_reachable() -> None:
    """A declared value nothing can return is a dead edge."""
    produced = {
        route_notification({"notify_eligible": 0}),
        route_notification({"notify_eligible": 1}),
    }
    assert produced == ROUTE_NOTIFICATION
