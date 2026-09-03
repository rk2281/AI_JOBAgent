"""The graph itself: seven nodes, three conditional edges, nothing else.

This is the only module in the project that imports langgraph. It sits
in app/workflows/ rather than app/integrations/ because the layering
rule quarantines vendor NETWORK clients -- anything that makes a call on
someone else's credentials -- and langgraph makes no call, holds no
credential and has no quota. It is a control-flow library, in the same
category as sqlalchemy in repositories and pydantic in schemas.

    START
      |
  resolve_targets --- nobody scorable ---------------+
      |                                              |
  discover_jobs      (self-skips, with a reason)     |
      |                                              |
  embed_jobs         (self-skips, with a reason)     |
      |                                              |
  enrich_jobs        (self-skips, with a reason)     |
      |                                              |
  score_and_rank --- no jobs / no users -------------+
      |                                              |
  decide_notification                                |
      |                                              |
   "notify" / "no_qualifying"                        |
      |                                              |
   finalise <----------------------------------------+
      |
     END

Only three edges are conditional. The skip decisions are inside the
nodes, so a skipped stage still has somebody to record WHY it was
skipped -- an edge that routes around a node leaves nobody to do that.

The two notification branches both point at finalise today. That is not
a stub: the routing rule is real and tested in both directions, and Day
11 changes one entry of NOTIFICATION_PATH_MAP to point "notify" at a
delivery node. Wiring it now is what makes the branch provably reachable
before the first message is ever sent to a person.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.workflows.nodes import (
    decide_notification,
    discover_jobs,
    embed_jobs,
    enrich_jobs,
    finalise,
    resolve_targets,
    score_and_rank,
)
from app.workflows.routing import (
    route_after_scoring,
    route_after_targets,
    route_notification,
)
from app.workflows.state import AgentState

# The node set, declared rather than inferred, so a test can assert the
# graph has exactly these and a node cannot be dropped without a
# failure. embed_jobs is the one that matters: it is absent from the
# plan's Day 9 row, and without it an ingest-then-score run scores none
# of the new jobs while its funnel balances perfectly.
NODE_NAMES = frozenset(
    {
        "resolve_targets",
        "discover_jobs",
        "embed_jobs",
        "enrich_jobs",
        "score_and_rank",
        "decide_notification",
        "finalise",
    }
)

# Path maps, built from the sets each router publishes. A typo'd target
# here is otherwise reported by LangGraph only at run time, on the
# branch that is never taken -- which for this graph is the branch
# nobody has ever executed.
TARGETS_PATH_MAP = {"discover_jobs": "discover_jobs", "finalise": "finalise"}
SCORING_PATH_MAP = {"decide_notification": "decide_notification", "finalise": "finalise"}

# Day 11 changes exactly one value here.
NOTIFICATION_PATH_MAP = {"notify": "finalise", "no_qualifying": "finalise"}


def build_graph():
    """Assemble and compile the workflow.

    The straight run of discover -> embed -> enrich -> score is
    deliberately not conditional. Every one of those nodes decides for
    itself whether to work, and returns a skip record when it does not,
    so the sequence stays fixed and observable.
    """
    graph = StateGraph(AgentState)

    graph.add_node("resolve_targets", resolve_targets)
    graph.add_node("discover_jobs", discover_jobs)
    graph.add_node("embed_jobs", embed_jobs)
    graph.add_node("enrich_jobs", enrich_jobs)
    graph.add_node("score_and_rank", score_and_rank)
    graph.add_node("decide_notification", decide_notification)
    graph.add_node("finalise", finalise)

    graph.add_edge(START, "resolve_targets")
    graph.add_conditional_edges("resolve_targets", route_after_targets, TARGETS_PATH_MAP)

    # Embedding before enrichment. Neither feeds the other --
    # build_job_document() reads only title and description -- but
    # enrichment is the stage that burns a daily quota and stops
    # mid-loop, while embedding is the stage that decides whether new
    # jobs are scorable at all. This way one 429 cannot make a whole
    # ingest invisible to scoring.
    graph.add_edge("discover_jobs", "embed_jobs")
    graph.add_edge("embed_jobs", "enrich_jobs")
    graph.add_edge("enrich_jobs", "score_and_rank")

    graph.add_conditional_edges("score_and_rank", route_after_scoring, SCORING_PATH_MAP)
    graph.add_conditional_edges(
        "decide_notification", route_notification, NOTIFICATION_PATH_MAP
    )

    graph.add_edge("finalise", END)

    return graph.compile()
