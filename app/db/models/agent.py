"""Bookkeeping for workflow runs -- what the GRAPH decided, not what the work did.

Every stage the graph drives already writes its own run row:
`ingestion_runs`, `embedding_runs`, `scoring_runs`. So nothing about the
WORK is unrecorded, and this table deliberately does not duplicate any
of it. What was unrecorded until now is the graph's own decisions --
which branch it took, what it skipped and why, whether it computed
anything at all -- and on Day 9 those were read by a person watching a
script print them. Day 10 runs the graph with nobody watching, and a
decision that only ever reached stdout is a decision nobody can audit
the next morning.

THE COLUMNS ARE DERIVED, NOT DESIGNED

Every column here corresponds to exactly one key of
`build_run_summary(state) -> dict` in app/workflows/state.py. That was
the whole argument for not building this table on Day 9: the summary was
made pure and separately tested so that Day 10 could persist a dict that
already existed rather than design a schema for a graph whose node set
was still moving. `test_agent_run_columns_cover_every_summary_key` holds
the two in step, driven off the model's actual columns and the summary's
actual keys rather than a list somebody maintains by hand.

TWO RULES INHERITED FROM THE SUMMARY, WHICH THIS TABLE MUST NOT BREAK

Absent is not zero. `jobs_enriched` and `enrichment_remaining_null` come
back None after a dry run because that path returns before computing
them; `users_skipped_*` come back None when scoring never ran at all.
Those columns are nullable and stay nullable. Defaulting them to 0 would
state an opinion the run never held -- the same mistake as defaulting an
abstained signal column to 0.0, which is a CLAUDE.md section 1 row.

The summary reads no clock. `finished_at` is stamped into state by the
`finalise` node BEFORE the summary is built, which is what makes two
calls on the same state identical. This table stores what it is handed;
neither the model nor its repository calls now().

WHY THE LIST COLUMNS ARE JSONB AND NOT ARRAY(Text)

Four of the five hold stage names and are homogeneous strings today, so
ARRAY(Text) would fit them. `errors` is the reason it is not used.
Nothing in app/workflows/ currently writes to state["errors"] at all --
see the module docstring note below -- so its list[str] annotation is
aspirational rather than enforced, and the first code that populates it
will be an exception path written on Day 11 or later. An exception
record is exactly the kind of thing that wants to be a dict. ARRAY(Text)
would make that a migration; JSONB makes it a Tuesday. One convention
for all five rather than two, because a reader should not have to
remember which of these is which.

WHAT AN INTERRUPTED RUN LEAVES BEHIND

A row with `started_at` set and `finished_at` NULL, and every counter
NULL. Not no row. See AgentRunRepository for why that shape was chosen
over a single write at the end, and note that "unfinished" is expressed
as `finished_at IS NULL` rather than as a status enum -- `scoring_runs`
distinguishes the same case the same way, and a status enum for
interrupted runs is Day 11's territory.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AgentRun(Base):
    """One pass of the workflow graph."""

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(primary_key=True)

    # --- opened at start ---------------------------------------------------
    #
    # started_at is the only non-null column besides id, because it is the
    # only thing known when the row is opened. Everything else arrives at
    # finish, and a run that never finishes legitimately has none of it.
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # TIMESTAMPTZ rather than Text although the summary hands over ISO-8601
    # STRINGS, not datetimes. They are timezone-aware and
    # datetime.fromisoformat() round-trips them exactly, so the repository
    # parses on the way in. Text would store them without a conversion, but
    # scoring_runs uses DateTime(timezone=True) and comparing the two
    # tables -- which is the obvious first question anyone asks of this
    # one -- would then need a cast on every query.

    # --- the graph's decisions --------------------------------------------
    status: Mapped[str | None] = mapped_column(String(32))
    terminal_reason: Mapped[str | None] = mapped_column(String(64))
    notify_branch: Mapped[str | None] = mapped_column(String(32))
    notify_eligible: Mapped[int | None] = mapped_column(Integer)

    # Always False in practice: a dry run does not write a row at all,
    # because a row saying writes_prevented=true would itself be a write
    # the run promised not to make. The column exists anyway, because the
    # drift test requires every summary key to have one, and because an
    # invariant that is visible in the schema is harder to break by
    # accident than one that lives only in a docstring.
    dry_run: Mapped[bool | None] = mapped_column(Boolean)
    writes_prevented: Mapped[bool | None] = mapped_column(Boolean)
    computation_performed: Mapped[bool | None] = mapped_column(Boolean)
    persistence_performed: Mapped[bool | None] = mapped_column(Boolean)

    user_id: Mapped[int | None] = mapped_column(Integer)

    # --- the four stage lists, plus errors ---------------------------------
    stages_attempted: Mapped[list | None] = mapped_column(JSONB)
    stages_skipped: Mapped[list | None] = mapped_column(JSONB)
    stages_computed: Mapped[list | None] = mapped_column(JSONB)
    stages_persisted: Mapped[list | None] = mapped_column(JSONB)
    errors: Mapped[list | None] = mapped_column(JSONB)

    # --- targets ------------------------------------------------------------
    users_considered: Mapped[int | None] = mapped_column(Integer)
    users_with_profile: Mapped[int | None] = mapped_column(Integer)
    users_with_embedded_cv: Mapped[int | None] = mapped_column(Integer)

    # --- per-stage outcomes -------------------------------------------------
    #
    # Deliberately NOT foreign keys to ingestion_runs / embedding_runs /
    # scoring_runs. A FK would make deleting an old run row fail against
    # this table, and this table is the audit trail -- it must survive the
    # thing it describes being cleaned up. The id is recorded so a reader
    # can go and look, not so the database can enforce that they still
    # agree.
    ingestion_status: Mapped[str | None] = mapped_column(String(32))
    ingestion_run_id: Mapped[int | None] = mapped_column(Integer)
    jobs_inserted: Mapped[int | None] = mapped_column(Integer)

    embedding_status: Mapped[str | None] = mapped_column(String(32))
    jobs_embedded: Mapped[int | None] = mapped_column(Integer)
    embeddings_remaining_null: Mapped[int | None] = mapped_column(Integer)

    enrichment_status: Mapped[str | None] = mapped_column(String(32))
    jobs_enriched: Mapped[int | None] = mapped_column(Integer)
    enrichment_remaining_null: Mapped[int | None] = mapped_column(Integer)

    scoring_status: Mapped[str | None] = mapped_column(String(32))
    scoring_run_id: Mapped[int | None] = mapped_column(Integer)

    # --- the scoring funnel, as the graph saw it ---------------------------
    #
    # users_skipped_no_cv counts three different things and keeps that name
    # on purpose -- see CLAUDE.md section 1. The three columns after it say
    # which cause applied. Only cv_not_embedded is fixable by running the
    # embedding pass.
    #
    # users_skipped_no_profile can never be non-zero through run_agent.py:
    # select_target_user_ids draws its ids from Profile.user_id, so every
    # id in the loop has a profile by construction, and the only way to
    # reach that branch -- --user-id X for an X with no profile --
    # terminates at no_scorable_users before scoring runs. A zero here is
    # therefore evidence of nothing, not evidence that no such user exists.
    users_skipped_no_cv: Mapped[int | None] = mapped_column(Integer)
    users_skipped_no_profile: Mapped[int | None] = mapped_column(Integer)
    users_skipped_no_active_cv: Mapped[int | None] = mapped_column(Integer)
    users_skipped_cv_not_embedded: Mapped[int | None] = mapped_column(Integer)

    users_scored: Mapped[int | None] = mapped_column(Integer)
    jobs_scored: Mapped[int | None] = mapped_column(Integer)
    pairs_scored: Mapped[int | None] = mapped_column(Integer)
    jobs_skipped_no_embedding: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = ({"comment": "One row per workflow graph run. See app/db/models/agent.py."},)

