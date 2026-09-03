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

import pytest

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
from app.core.config import assert_tracing_disabled, tracing_vars_set
from app.workflows.state import initial_state

_WORKFLOWS_DIR = Path(__file__).resolve().parents[1] / "app" / "workflows"


def imported_modules(source: str) -> list[str]:
    """Every module name imported by `source`, in order.

    Takes source TEXT rather than a path, and that is the whole point of
    it existing. The two layering tests below previously walked the AST
    inline and read `node.names[0].name` for an `ast.Import`, so they saw
    only the FIRST alias of a multi-alias import: `import langgraph` was
    caught, `import json, langgraph` was not.

    No module in app/workflows/ has a comma-separated import, so both of
    those tests passed before the fix and pass after it. The hole was
    therefore invisible from disk, and the only thing that can tell a
    fixed parser from a broken one is source it has never seen. Hence a
    string argument: the tests below feed it real files, and the tests
    beside it feed it synthetic imports that no file in this repository
    contains.

    `from x import a, b` contributes `x` once -- the module is what the
    layering rules are about, not the names taken out of it. A bare
    `from . import x` has no module and contributes nothing.
    """
    names: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            if node.module:
                names.append(node.module)
        elif isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
    return names


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
        for module in imported_modules(path.read_text(encoding="utf-8")):
            if module.startswith(forbidden_prefixes):
                offenders.append(f"{path.name} imports {module}")

    assert not offenders, offenders


def test_langgraph_is_imported_in_exactly_one_module() -> None:
    """Sequencing lives in one file. If a second module grows a
    langgraph import, a node has started making routing decisions of
    its own."""
    importers = []
    for path in sorted(_WORKFLOWS_DIR.glob("*.py")):
        for module in imported_modules(path.read_text(encoding="utf-8")):
            if module.startswith("langgraph"):
                importers.append(path.name)
    # Imports, not a text search: __init__.py explains in prose why
    # langgraph lives in this package, and prose is not a dependency.
    assert sorted(set(importers)) == ["graph.py"]


# --- the import parser both layering tests depend on ---------------------
#
# Driven against synthetic source, never against a file on disk. Both
# layering tests above pass with a parser that reads only the first alias
# of a multi-alias import, because no module in app/workflows/ has one --
# so a file-driven test cannot tell a fixed parser from a broken one.
# These can.


def test_a_multi_alias_import_yields_every_name() -> None:
    """The exact hole this helper was extracted to close.

    `node.names[0].name` returns ["json"] here and the forbidden name
    walks straight through a layering test that reports success.
    """
    assert imported_modules("import json, langgraph") == ["json", "langgraph"]


def test_three_aliases_all_survive() -> None:
    assert imported_modules("import os, sys, sqlalchemy") == ["os", "sys", "sqlalchemy"]


def test_a_single_alias_import_still_works() -> None:
    assert imported_modules("import langgraph") == ["langgraph"]


def test_dotted_names_are_returned_whole() -> None:
    """The layering tests match on prefixes, so a truncated name would
    silently stop matching `app.db`."""
    assert imported_modules("import app.db.session") == ["app.db.session"]


def test_from_import_yields_the_module_once() -> None:
    """The module is what the layering rules are about, not the names
    taken out of it."""
    assert imported_modules("from app.db.repositories import CVRepository, JobRepository") == [
        "app.db.repositories"
    ]


def test_an_aliased_import_reports_the_real_module_not_the_alias() -> None:
    """`import sqlalchemy as sa` must not hide behind `sa`."""
    assert imported_modules("import sqlalchemy as sa") == ["sqlalchemy"]


def test_from_import_with_an_alias_still_reports_the_module() -> None:
    assert imported_modules("from app.bot import handlers as h") == ["app.bot"]


def test_source_with_no_imports_returns_an_empty_list() -> None:
    assert imported_modules("x = 1\n\n\ndef f():\n    return x\n") == []


def test_empty_source_returns_an_empty_list() -> None:
    assert imported_modules("") == []


def test_a_relative_import_with_no_module_contributes_nothing() -> None:
    """`from . import x` has node.module None; it names no module to
    check a layering rule against."""
    assert imported_modules("from . import nodes") == []


def test_imports_nested_inside_a_function_are_found() -> None:
    """A forbidden import moved inside a function body is still a
    dependency. ast.walk reaches it; a scan of module.body would not."""
    source = "def f():\n    import sqlalchemy\n    return sqlalchemy\n"
    assert imported_modules(source) == ["sqlalchemy"]


def test_the_helper_catches_what_the_layering_test_looks_for() -> None:
    """End to end on the exact shape that used to slip through: the
    forbidden prefix is the SECOND alias."""
    forbidden_prefixes = ("app.db", "sqlalchemy", "app.bot", "app.services.scoring_signals")
    found = [
        module
        for module in imported_modules("import json, sqlalchemy")
        if module.startswith(forbidden_prefixes)
    ]
    assert found == ["sqlalchemy"]


# --- the tracer fails closed ---------------------------------------------
#
# langsmith arrives as a langchain-core dependency and activates from the
# process environment alone. Searching this repository proves it does not
# set those variables; it proves nothing about the machine the graph runs
# on, and that is the case that matters once Day 10 runs it unattended.
# An enabled tracer ships graph state -- CV-derived profile text and job
# descriptions -- to a third party.
#
# The first group drives the pure function against dicts. The last two
# drive build_graph() through a real process environment: the work, not
# the plan.


def test_a_clean_environment_reports_nothing() -> None:
    assert tracing_vars_set({}) == []
    assert tracing_vars_set({"PATH": "/usr/bin", "HOME": "/home/x"}) == []


@pytest.mark.parametrize(
    "name",
    [
        "LANGCHAIN_TRACING_V2",
        "LANGSMITH_TRACING",
        "LANGCHAIN_API_KEY",
        "LANGSMITH_API_KEY",
        "LANGCHAIN_ENDPOINT",
        "LANGSMITH_ENDPOINT",
        "LANGCHAIN_PROJECT",
        "LANGSMITH_PROJECT",
    ],
)
def test_both_spellings_of_every_name_are_checked(name: str) -> None:
    """langchain-core renamed LANGCHAIN_* to LANGSMITH_* and still
    honours the old names. Checking only the newer pair would return a
    clean answer while tracing was on."""
    assert tracing_vars_set({name: "x"}) == [name]


def test_an_empty_value_counts_as_unset() -> None:
    """The exact boundary: empty string, not merely a short one. This is
    how langchain-core reads them."""
    assert tracing_vars_set({"LANGCHAIN_TRACING_V2": ""}) == []


def test_a_whitespace_only_value_counts_as_unset() -> None:
    assert tracing_vars_set({"LANGSMITH_API_KEY": "   "}) == []
    assert tracing_vars_set({"LANGSMITH_API_KEY": "\t\n"}) == []


def test_one_character_past_the_boundary_counts_as_set() -> None:
    """The value either side of "empty after stripping"."""
    assert tracing_vars_set({"LANGCHAIN_TRACING_V2": " x "}) == ["LANGCHAIN_TRACING_V2"]


def test_a_falsy_looking_value_still_counts_as_set() -> None:
    """Deliberate and stricter than langchain-core: disabling tracing by
    VALUE rather than by unsetting is reported. Failing closed is the
    direction to be wrong in when the alternative is exporting CV text.
    """
    assert tracing_vars_set({"LANGCHAIN_TRACING_V2": "false"}) == ["LANGCHAIN_TRACING_V2"]


def test_every_name_set_is_reported_not_just_the_first() -> None:
    """A diagnostic that stopped at the first match would send somebody
    to unset one variable and leave the tracer running on another."""
    found = tracing_vars_set(
        {
            "LANGCHAIN_TRACING_V2": "true",
            "LANGSMITH_API_KEY": "ls-secret",
            "LANGSMITH_PROJECT": "p",
        }
    )
    assert found == ["LANGCHAIN_TRACING_V2", "LANGSMITH_API_KEY", "LANGSMITH_PROJECT"]


def test_the_output_is_sorted_so_the_message_is_reproducible() -> None:
    found = tracing_vars_set({"LANGSMITH_TRACING": "1", "LANGCHAIN_API_KEY": "k"})
    assert found == sorted(found)


def test_the_report_names_variables_and_never_values() -> None:
    """Two of these names are credentials. CLAUDE.md section 3: nine leak
    incidents, none from printing .env, every one from a secret handled
    incidentally. A diagnostic that echoed what it found would be the
    tenth."""
    found = tracing_vars_set({"LANGSMITH_API_KEY": "ls-super-secret-value"})
    assert found == ["LANGSMITH_API_KEY"]
    assert "ls-super-secret-value" not in " ".join(found)


def test_assert_tracing_disabled_is_quiet_on_a_clean_environment() -> None:
    assert_tracing_disabled({})


def test_assert_tracing_disabled_raises_rather_than_warning() -> None:
    """It raises because a warning about telemetry is read after the run
    that already sent the data."""
    with pytest.raises(RuntimeError):
        assert_tracing_disabled({"LANGCHAIN_TRACING_V2": "true"})


def test_build_graph_refuses_while_tracing_is_enabled(monkeypatch) -> None:
    """The work, not the plan: a real process environment through the
    real call, not a dict through a pure function."""
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    with pytest.raises(RuntimeError) as excinfo:
        build_graph()
    assert "LANGCHAIN_TRACING_V2" in str(excinfo.value)


def test_build_graph_names_the_variable_and_not_its_value(monkeypatch) -> None:
    """A planted key must appear by NAME in the message and its value
    must not appear anywhere in it."""
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2-pt-planted-secret-do-not-print")
    with pytest.raises(RuntimeError) as excinfo:
        build_graph()

    message = str(excinfo.value)
    assert "LANGSMITH_API_KEY" in message
    assert "lsv2-pt-planted-secret-do-not-print" not in message


def test_build_graph_still_compiles_with_tracing_unset(monkeypatch) -> None:
    """The check must not be a permanent refusal. Unset every name and
    the graph builds as before."""
    for name in (
        "LANGCHAIN_TRACING_V2",
        "LANGSMITH_TRACING",
        "LANGCHAIN_API_KEY",
        "LANGSMITH_API_KEY",
        "LANGCHAIN_ENDPOINT",
        "LANGSMITH_ENDPOINT",
        "LANGCHAIN_PROJECT",
        "LANGSMITH_PROJECT",
    ):
        monkeypatch.delenv(name, raising=False)
    assert build_graph() is not None
