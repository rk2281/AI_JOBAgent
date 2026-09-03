"""Day 9: the existing pipeline, run as one stateful unit.

Nothing in this package computes anything. Ingestion validates and
deduplicates inside JobIngestionService; scoring matches, scores, ranks
and decides notification inside run_scoring. Both already own their
transactions and assert their own funnels. Rebuilding any of that as a
graph node would produce a second implementation of something that
currently has exactly one.

What this package adds is sequencing, and making the branch points
explicit instead of leaving them implicit in a script's control flow.
A node is a unit of ORCHESTRATION, not a unit of computation.

Why this is app/workflows/ and not app/integrations/: the layering rule
quarantines vendor network clients -- things that make a network call
on someone else's credentials -- not every third-party package.
langgraph makes no network call, holds no credential and has no quota,
so it belongs here for the same reason sqlalchemy belongs in
repositories. And why app/workflows/ rather than a new app/agent/:
CODEBASE_GUIDE.md already reserves this name for "scheduled matching
runs". A second name for the same layer would leave the reserved one
empty forever.
"""
