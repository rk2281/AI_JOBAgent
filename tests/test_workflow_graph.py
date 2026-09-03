"""The assembled graph: its shape, its layering, and its unrun branch.

Three things are being protected here.

First, that embed_jobs cannot silently disappear. It is absent from the
plan's Day 9 row, so the pressure to drop it is real, and dropping it
produces no failure -- an ingest-then-score run would score none of the
new jobs while `pairs_scored == jobs_scored x users_scored` balanced
perfectly. The test walks every path from discover_jobs to
score_and_rank and fails if any of them misses embedding. It fails on
deletion AND on rewiring, which "the node exists" would not.

Second, that the notify branch is reachable. Day 11 attaches real
Telegram delivery to an edge that has never executed against real data
-- with weight_covered at 0.50 against a 0.55 floor, no pair can
currently clear the gate. So it is proven here with a stubbed scoring
result, in seconds, rather than discovered the first time it sends a
message to a person.

Note WHY that has to be proven by execution rather than by reading
edges: both branches currently point at finalise, and LangGraph draws
them as a single edge. The wiring is asserted separately against
NOTIFICATION_PATH_MAP.

Third, that the graph stays a graph. No SQL, no repository, no ORM, no
Telegram anywhere under app/workflows/.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

from app.workflows import nodes
from app.workflows.graph import (
    NODE_NAMES,
    NOTIFICATION_PATH_MAP,
    SCORING_PATH_MAP,
    TARGETS_PATH_MAP,
    build_graph,
)
from app.workflows.routing import (
    ROUTE_AFTER_SCORING,
    ROUTE_AFTER_TARGETS,
    ROUTE_NOTIFICATION,
)
from app.workflows.state import initial_state

_WORKFLOWS_DIR = Path(__file__).resolve().parents[1] / "app" / "workflows"


def _run(coro):
    return asyncio.run(coro)


def _drawn():
    return build_graph().get_graph()


def _all_paths(edges, start: str, goal: str) -> list[list[str]]:
    """Every simple path from start to goal, as node-name lists."""
    outgoing: dict[str, list[str]] = {}
    for edge in edges:
        outgoing.setdefault(edge.source, []).append(edge.target)

    paths: list[list[str]] = []

    def walk(node: str, seen: list[str]) -> None:
        if node == goal:
            paths.append(seen + [node])
            return
        for nxt in outgoing.get(node, []):
            if nxt not in seen:
                walk(nxt, seen + [node])

    walk(start, [])
    return paths


# --- shape ---------------------------------------------------------------


def test_the_graph_compiles() -> None:
    assert build_graph() is not None


def test_the_graph_has_exactly_the_declared_nodes() -> None:
    drawn = set(_drawn().nodes) - {"__start__", "__end__"}
    assert drawn == set(NODE_NAMES)


def test_embed_jobs_is_a_node_at_all() -> None:
    """Weak on its own -- the path test below is the real one -- but it
    localises the failure when someone deletes the node outright."""
    assert "embed_jobs" in _drawn().nodes


def test_embed_jobs_is_on_every_path_from_discovery_to_scoring() -> None:
    """The anti-disappearance test.

    A graph that ingests and then scores without embedding in between
    would ingest twenty jobs and score none of them, because
    run_scoring skips every job whose embedding is NULL. Nothing about
    the run would look wrong: the funnel would balance perfectly while
    doing it.
    """
    paths = _all_paths(_drawn().edges, "discover_jobs", "score_and_rank")
    assert paths, "no path from discover_jobs to score_and_rank at all"
    for path in paths:
        assert "embed_jobs" in path, f"path skips embedding: {path}"


def test_embedding_runs_before_enrichment() -> None:
    """Neither feeds the other, so this ordering is a quota decision:
    enrichment stops mid-loop on a 429, and embedding is what makes
    newly ingested jobs scorable at all."""
    for path in _all_paths(_drawn().edges, "discover_jobs", "score_and_rank"):
        assert path.index("embed_jobs") < path.index("enrich_jobs")


def test_every_node_can_reach_finalise() -> None:
    """A node with no route to the terminal node is a run that hangs."""
    edges = _drawn().edges
    for node in NODE_NAMES:
        if node == "finalise":
            continue
        assert _all_paths(edges, node, "finalise"), f"{node} cannot reach finalise"


def test_finalise_is_the_only_way_out() -> None:
    ends = [edge.source for edge in _drawn().edges if edge.target == "__end__"]
    assert ends == ["finalise"]


# --- path maps match what the routers can return -------------------------


def test_every_router_output_has_an_edge_target() -> None:
    """Catches a typo'd path-map key, which LangGraph otherwise reports
    only at run time on the branch that is never taken."""
    assert set(TARGETS_PATH_MAP) == set(ROUTE_AFTER_TARGETS)
    assert set(SCORING_PATH_MAP) == set(ROUTE_AFTER_SCORING)
    assert set(NOTIFICATION_PATH_MAP) == set(ROUTE_NOTIFICATION)


def test_every_path_map_target_is_a_real_node() -> None:
    for path_map in (TARGETS_PATH_MAP, SCORING_PATH_MAP, NOTIFICATION_PATH_MAP):
        for target in path_map.values():
            assert target in NODE_NAMES


def test_both_notification_branches_are_wired_today() -> None:
    """Day 11 changes exactly one value here. Recorded so that change
    is a one-line diff rather than a new branch nobody has run."""
    assert NOTIFICATION_PATH_MAP == {"notify": "finalise", "no_qualifying": "finalise"}


# --- the branch Day 11 depends on ----------------------------------------


def _stub_everything(monkeypatch, *, notify_eligible: int) -> None:
    async def fake_targets(*, user_id=None):
        return {
            "requested_user_id": user_id,
            "users_considered": 1,
            "users_with_profile": 1,
            "users_with_embedded_cv": 1,
            "target_user_ids": [2],
        }

    async def fake_scoring(*, user_id=None, dry_run=False):
        return {
            "status": "complete" if notify_eligible else "complete_no_qualifying",
            "run_id": 1,
            "users_scored": 1,
            "jobs_scored": 98,
            "pairs_scored": 98,
            "notify_eligible": notify_eligible,
        }

    async def fake_enrichment(*, limit=None, dry_run=False):
        return {"status": "complete", "attempted": 0, "candidates_considered": 97}

    monkeypatch.setattr(nodes, "resolve_scoring_targets", fake_targets)
    monkeypatch.setattr(nodes, "run_scoring", fake_scoring)
    # Enrichment is the one stage a dry run still CALLS, so it has to be
    # stubbed too or these tests reach for a database.
    monkeypatch.setattr(nodes, "run_enrichment", fake_enrichment)


def test_the_notify_branch_is_reachable(monkeypatch) -> None:
    """No database, no quota, no enrichment -- and the branch Day 11
    hangs Telegram off is proven to execute."""
    _stub_everything(monkeypatch, notify_eligible=3)

    final = _run(
        build_graph().ainvoke(
            initial_state(dry_run=True, started_at="2026-09-03T00:00:00+00:00")
        )
    )

    assert final["notify_branch"] == "notify"
    assert final["notify_eligible"] == 3
    assert final["finished_at"] is not None


def test_the_quiet_branch_is_reachable(monkeypatch) -> None:
    """The branch that IS taken with today's real data."""
    _stub_everything(monkeypatch, notify_eligible=0)

    final = _run(
        build_graph().ainvoke(
            initial_state(dry_run=True, started_at="2026-09-03T00:00:00+00:00")
        )
    )

    assert final["notify_branch"] == "no_qualifying"
    assert final["notify_eligible"] == 0


def test_a_run_with_nobody_scorable_never_reaches_scoring(monkeypatch) -> None:
    """It must stop before spending an Adzuna pass and a day of Gemini
    quota discovering there was nobody to score for."""

    async def fake_targets(*, user_id=None):
        return {
            "requested_user_id": user_id,
            "users_considered": 3,
            "users_with_profile": 3,
            "users_with_embedded_cv": 0,
            "target_user_ids": [],
        }

    def never(*args, **kwargs):
        raise AssertionError("must not run when nobody is scorable")

    monkeypatch.setattr(nodes, "resolve_scoring_targets", fake_targets)
    monkeypatch.setattr(nodes, "run_scoring", never)
    monkeypatch.setattr(nodes, "run_ingestion", never)
    monkeypatch.setattr(nodes, "run_enrichment", never)
    monkeypatch.setattr(nodes, "run_job_embedding", never)

    final = _run(
        build_graph().ainvoke(initial_state(started_at="2026-09-03T00:00:00+00:00"))
    )

    assert final["terminal_reason"] == "no_scorable_users"
    assert final["notify_branch"] is None
    assert final["scoring"] is None


def test_a_dry_run_skips_ingestion_and_embedding_end_to_end(monkeypatch) -> None:
    """The whole graph, and the two skips still carry their reasons."""
    _stub_everything(monkeypatch, notify_eligible=0)

    def never(*args, **kwargs):
        raise AssertionError("must not run under dry_run")

    monkeypatch.setattr(nodes, "run_ingestion", never)
    monkeypatch.setattr(nodes, "AdzunaClient", never)
    monkeypatch.setattr(nodes, "run_job_embedding", never)

    async def fake_enrichment(*, limit=None, dry_run=False):
        assert dry_run is True
        return {"status": "complete", "attempted": 0, "candidates_considered": 97}

    monkeypatch.setattr(nodes, "run_enrichment", fake_enrichment)

    final = _run(
        build_graph().ainvoke(
            initial_state(dry_run=True, started_at="2026-09-03T00:00:00+00:00")
        )
    )

    assert "discover_jobs: dry_run" in final["stages_skipped"]
    assert "embed_jobs: dry_run" in final["stages_skipped"]
    assert "enrich_jobs" in final["stages_attempted"]
    assert "score_and_rank" in final["stages_attempted"]


# --- the graph stays a graph ---------------------------------------------


def test_no_workflow_module_imports_the_database_or_telegram() -> None:
    """app/workflows/ orchestrates. It does not query, and it does not
    talk to Telegram.

    resolve_targets needs to know which users are scorable, and it asks
    a SERVICE -- app.services.job_scoring.resolve_scoring_targets --
    rather than importing a repository. That is why this test needs no
    exception clause: an exception is a hole for the rule to grow into.
    """
    forbidden_prefixes = ("app.db", "sqlalchemy", "app.bot", "app.services.scoring_signals")

    offenders: list[str] = []
    for path in sorted(_WORKFLOWS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                module = node.module
            elif isinstance(node, ast.Import):
                module = node.names[0].name
            else:
                continue
            if module.startswith(forbidden_prefixes):
                offenders.append(f"{path.name} imports {module}")

    assert not offenders, offenders


def test_langgraph_is_imported_in_exactly_one_module() -> None:
    """Sequencing lives in one file. If a second module grows a
    langgraph import, a node has started making routing decisions of
    its own."""
    importers = []
    for path in sorted(_WORKFLOWS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                module = node.module
            elif isinstance(node, ast.Import):
                module = node.names[0].name
            else:
                continue
            if module.startswith("langgraph"):
                importers.append(path.name)
    # Imports, not a text search: __init__.py explains in prose why
    # langgraph lives in this package, and prose is not a dependency.
    assert sorted(set(importers)) == ["graph.py"]
