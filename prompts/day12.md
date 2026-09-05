# ULTIMATE MASTER PROMPT — AI JOB HUNTING AGENT

## Day 12: Complete, Fix, Verify & Production-Ready MVP Finalization

You are acting as a **Senior AI/ML Engineer + Backend Engineer + Software Architect + QA Engineer + DevOps Engineer + Code Reviewer**.

You have been given:

1. The existing **AI Job Hunting & Recommendation Agent codebase**
2. The **12-Day Implementation Plan**
3. Existing project documentation and tests

Your responsibility is to take the CURRENT implementation exactly as it exists and complete **Day 12 as a full end-to-end engineering finalization phase**.

---

# 1. PRIMARY OBJECTIVE

Do NOT blindly add new features.

Do NOT rewrite the project from scratch.

Do NOT assume that because a component exists, it is working.

Your job is to:

> **Inspect → Identify → Fix → Integrate → Test → Verify → Document → Clean → Demonstrate**

The final result must be a **working, reproducible, tested and explainable MVP** of the AI Job Hunting Agent.

The final system should demonstrate the complete pipeline:

```text
Telegram User
      ↓
CV Upload
      ↓
CV Extraction
      ↓
Candidate Profile Generation
      ↓
Profile Persistence
      ↓
Job Discovery / Ingestion
      ↓
Job Validation
      ↓
Job Deduplication
      ↓
Job Normalization
      ↓
Embedding Generation
      ↓
pgvector Semantic Retrieval
      ↓
Traditional Matching
      ↓
Hybrid Scoring
      ↓
Ranking
      ↓
LangGraph Agent Workflow
      ↓
Notification Decision
      ↓
Telegram Notification
      ↓
User Feedback
      ↓
PostgreSQL
```

Every major stage must be **implemented, connected, executable and verified**.

---

# 2. VERY IMPORTANT: FIRST AUDIT THE EXISTING PROJECT

Before changing code, perform a complete repository audit.

Inspect:

- directory structure
- Python modules
- FastAPI application
- configuration
- environment variables
- database models
- migrations
- repositories/services
- CV processing
- LLM integration
- embeddings
- pgvector
- job ingestion
- validation
- deduplication
- matching
- scoring
- ranking
- LangGraph
- scheduler
- Telegram bot
- notification system
- feedback system
- tests
- scripts
- documentation
- logging
- error handling
- Docker/configuration if present

Do not rely on filenames alone.

Trace the actual execution flow.

For every important component determine:

```text
Implemented?
Connected?
Executable?
Tested?
Integration-tested?
Production-safe?
Documented?
```

Create an internal gap matrix:

| Component         | Exists | Connected | Tested | Integration Verified | Issue |
| ----------------- | ------ | --------- | ------ | -------------------- | ----- |
| FastAPI           |        |           |        |                      |       |
| Telegram          |        |           |        |                      |       |
| CV extraction     |        |           |        |                      |       |
| Candidate profile |        |           |        |                      |       |
| PostgreSQL        |        |           |        |                      |       |
| pgvector          |        |           |        |                      |       |
| Job ingestion     |        |           |        |                      |       |
| Validation        |        |           |        |                      |       |
| Deduplication     |        |           |        |                      |       |
| Embeddings        |        |           |        |                      |       |
| Semantic search   |        |           |        |                      |       |
| Matching          |        |           |        |                      |       |
| Scoring           |        |           |        |                      |       |
| Ranking           |        |           |        |                      |       |
| LangGraph         |        |           |        |                      |       |
| Scheduler         |        |           |        |                      |       |
| Notifications     |        |           |        |                      |       |
| Feedback          |        |           |        |                      |       |
| Logging           |        |           |        |                      |       |
| Tests             |        |           |        |                      |       |
| Documentation     |        |           |        |                      |       |

---

# 3. DO NOT DESTROY EXISTING ARCHITECTURE

The current architecture was intentionally designed around:

- Python
- FastAPI
- PostgreSQL
- pgvector
- LLM/NLP
- embeddings
- semantic retrieval
- traditional matching
- hybrid scoring
- LangGraph
- scheduler
- Telegram
- Redis/Celery/APScheduler where already implemented

Preserve this architecture unless there is a genuine engineering reason to modify it.

If something is imperfect but acceptable for the MVP:

> Document it rather than unnecessarily rewriting it.

If something is genuinely broken:

> Fix the root cause.

Avoid unnecessary refactoring.

---

# 4. DAY 12 WORKSTREAM 1 — CLEAN REMAINING ISSUES

Inspect all known issues from previous implementation days.

Pay particular attention to:

### A. Scoring / Abstention

Review how the system behaves when job information is missing.

For example:

```text
Skill requirement unavailable
Experience unavailable
Location available
Semantic similarity available
Title available
```

Determine whether weight renormalization can produce misleadingly high scores.

Do NOT arbitrarily change the algorithm.

Instead:

1. Understand the current intended behavior.
2. Determine whether it is a bug or intentional design.
3. If broken, fix it.
4. If intentional, document it.
5. Add tests for edge cases.

Test:

- complete job data
- missing skills
- missing experience
- missing location
- missing description
- missing salary
- incomplete candidate profile

---

# 5. DAY 12 WORKSTREAM 2 — FULL TEST SUITE

Run the complete test suite.

Do not merely report:

```text
Tests exist.
```

Actually execute them.

Record:

```text
Total tests
Passed
Failed
Skipped
Errors
Warnings
Execution time
```

For every failure:

```text
Failure
    ↓
Root cause
    ↓
Fix
    ↓
Re-run
```

Do not suppress failures simply to obtain a green test report.

Tests must be meaningful.

---

# 6. ADD MISSING INTEGRATION TESTS

Existing unit tests are not enough.

Create integration tests covering the actual system flow.

At minimum:

### Test 1 — Candidate pipeline

```text
CV
 ↓
Extraction
 ↓
Structured profile
 ↓
Database persistence
```

### Test 2 — Job pipeline

```text
Raw job
 ↓
Validation
 ↓
Normalization
 ↓
Deduplication
 ↓
Database
```

### Test 3 — Recommendation pipeline

```text
Candidate
 ↓
Embedding
 ↓
pgvector retrieval
 ↓
Matching
 ↓
Scoring
 ↓
Ranking
```

### Test 4 — Agent pipeline

```text
Candidate
 ↓
Retrieved jobs
 ↓
LangGraph
 ↓
Decision
 ↓
Notification eligibility
```

### Test 5 — Feedback pipeline

```text
Telegram interaction
 ↓
Feedback
 ↓
Database
 ↓
Correct persistence
```

---

# 7. CRITICAL — FULL END-TO-END TEST

This is the most important Day-12 requirement.

Perform one genuine end-to-end execution.

Use a realistic test candidate.

Example:

```text
Candidate:
AI/ML Engineer

Skills:
Python
Machine Learning
NLP
FastAPI
SQL

Experience:
1 year

Preferred location:
Delhi NCR

Preferred roles:
AI Engineer
ML Engineer
Machine Learning Engineer
```

Then execute:

```text
CV upload
      ↓
Profile extraction
      ↓
Profile storage
      ↓
Job ingestion
      ↓
Validation
      ↓
Deduplication
      ↓
Embedding generation
      ↓
Vector retrieval
      ↓
Matching
      ↓
Scoring
      ↓
Ranking
      ↓
LangGraph
      ↓
Notification decision
      ↓
Telegram
      ↓
Feedback
      ↓
Database
```

Do not fake any stage.

Do not replace real components with mocks for the final E2E verification.

Mocks may be used in unit tests, but the final demonstration must exercise the real system wherever credentials/infrastructure permit.

---

# 8. VERIFY DATABASE STATE

After the E2E run, inspect PostgreSQL.

Verify that expected records exist.

Check relevant entities such as:

```text
users
candidate profiles
CV records
preferences
jobs
job embeddings
recommendations
notifications
feedback
agent/workflow state
```

Verify relationships.

Verify no unexpected duplication.

Verify timestamps.

Verify status transitions.

Verify foreign-key relationships.

Verify idempotency where applicable.

The final verification should answer:

> "Can I trace one user from CV upload all the way to notification and feedback using the database?"

If not, fix it.

---

# 9. VERIFY PGVECTOR

Explicitly verify:

```text
Embedding generated
        ↓
Stored correctly
        ↓
Correct dimensionality
        ↓
Vector index/search works
        ↓
Similarity retrieval returns sensible jobs
```

Perform at least one manual/automated retrieval inspection.

Show:

```text
Candidate
Job
Similarity score
Ranking
```

Make sure embeddings aren't silently failing or returning meaningless results.

---

# 10. VERIFY HYBRID MATCHING

Validate that semantic similarity is not the only factor.

Verify the final ranking incorporates the intended signals, such as:

```text
Semantic similarity
Skill match
Experience match
Location match
Title/role match
Other configured preferences
```

Document the scoring formula actually implemented.

Do not invent a formula that doesn't exist in code.

If the implementation differs from documentation:

> Update the documentation to reflect reality.

---

# 11. VERIFY LANGGRAPH

Trace the actual graph.

Document:

```text
START
 ↓
Profile/Context preparation
 ↓
Job retrieval
 ↓
Validation/filtering
 ↓
Scoring/ranking
 ↓
Notification decision
 ↓
END
```

Use the actual node names from the code.

Verify:

- state structure
- transitions
- conditional routing
- failure paths
- empty-result handling
- notification gate
- final state

Add tests for:

```text
Relevant job
Irrelevant job
No jobs
Low score
High score
Incomplete data
Notification failure
```

---

# 12. VERIFY TELEGRAM

Perform a real Telegram flow if credentials are available.

Verify:

```text
/start
 ↓
Onboarding
 ↓
CV upload
 ↓
Preferences
 ↓
Processing
 ↓
Recommendation
 ↓
Notification
 ↓
Feedback
```

Verify Telegram errors are handled gracefully.

Verify duplicate notifications are prevented where intended.

Verify feedback callbacks/buttons actually reach the backend.

Verify feedback reaches PostgreSQL.

Do not claim Telegram is verified if it was only unit-tested.

Clearly distinguish:

```text
Unit verified
Integration verified
Real external service verified
```

---

# 13. VERIFY SCHEDULER

The scheduler is an important part of the automation layer.

Verify the actual scheduler mechanism currently implemented.

For example:

```text
Scheduler
 ↓
Application/script
 ↓
Recommendation pipeline
 ↓
Notification
```

Verify:

- correct Python interpreter
- correct working directory
- environment loading
- logs
- exit codes
- failure behavior
- duplicate/overlapping execution behavior
- scheduler actually triggers the job

If Windows Task Scheduler is being used, perform an actual trigger test if the environment allows it.

Do not merely inspect the `.ps1` script.

Record:

```text
Trigger time
Start time
End time
Exit code
Logs
Records generated
Notifications generated
```

---

# 14. ERROR HANDLING

Perform a deliberate failure test.

Test things like:

```text
Invalid CV
Corrupted CV
LLM failure
Embedding failure
Database unavailable
Job source unavailable
Invalid job URL
Duplicate job
Telegram unavailable
Empty search results
Malformed job data
```

The application should fail gracefully.

It should:

- log the error
- preserve useful context
- avoid crashing unrelated components
- retry where appropriate
- return sensible errors
- avoid silently swallowing exceptions

Do not add excessive retry logic blindly.

---

# 15. LOGGING & OBSERVABILITY

Make sure important operations are observable.

At minimum log:

```text
request/event
user/candidate identifier where appropriate
pipeline stage
job identifier
processing status
errors
latency
notification status
```

Avoid logging:

- API keys
- bot tokens
- passwords
- full CV contents unnecessarily
- private credentials

If token/latency tracking already exists, verify it.

If not, implement lightweight useful measurements rather than an oversized observability system.

---

# 16. SECURITY AUDIT

Perform a final security audit.

Check for:

```text
.env files
hardcoded API keys
Telegram bot tokens
database passwords
credentials
private CVs
local storage
logs containing secrets
```

The final shareable repository MUST NOT contain:

```text
.env
real credentials
real CV files
private user data
secrets
logs containing sensitive data
```

Create/update:

```text
.env.example
```

with placeholders only.

If credentials appear to have been exposed during development, recommend rotation.

---

# 17. REPOSITORY CLEANUP

Clean unnecessary files.

Remove:

```text
temporary scripts
debug files
cache
generated artifacts
large datasets
private documents
logs
local databases
credentials
duplicate documentation
```

Do not remove anything required for reproducibility.

The final structure should be clean and understandable.

Example:

```text
AI_JOB_HUNT_AGENT/
│
├── app/
├── tests/
├── migrations/
├── scripts/
├── docs/
├── storage/
│   └── .gitkeep
│
├── .env.example
├── .gitignore
├── requirements.txt
├── README.md
└── docker-compose.yml       # if applicable
```

Adapt this to the actual project.

---

# 18. UPDATE README

The README must allow another engineer to understand and run the system.

Include:

### Project Overview

What problem does it solve?

### Architecture

Explain the complete architecture.

### Tech Stack

Explain why each major technology exists.

### Installation

Exact setup commands.

### Environment Variables

Explain `.env.example`.

### Database

Migration/setup instructions.

### Telegram

Bot setup instructions.

### Running the API

Exact command.

### Running the scheduler

Exact command.

### Running tests

Exact command.

### End-to-End Flow

Explain the complete lifecycle.

### Troubleshooting

Document common failures.

---

# 19. CREATE FINAL TECHNICAL DOCUMENTATION

Create:

```text
docs/FINAL_ARCHITECTURE.md
docs/TECHNICAL_DECISIONS.md
docs/MATCHING_AND_SCORING.md
docs/TEST_RESULTS.md
docs/END_TO_END_VERIFICATION.md
docs/MVP_LIMITATIONS.md
docs/MANAGER_DEMO.md
```

These documents should be based on the ACTUAL implementation.

Do not write theoretical documentation disconnected from the code.

---

# 20. FINAL ARCHITECTURE DOCUMENT

Explain:

```text
Telegram
   ↓
FastAPI
   ↓
Services
   ↓
PostgreSQL
   ↓
Job Intelligence Layer
   ↓
Embedding + pgvector
   ↓
Matching
   ↓
LangGraph
   ↓
Notification
   ↓
Feedback
```

Include responsibilities of every component.

Explain why each technology was selected.

---

# 21. TECHNICAL DECISION DOCUMENT

Document important decisions such as:

### Why FastAPI?

### Why PostgreSQL?

### Why pgvector?

### Why embeddings?

### Why semantic retrieval?

### Why traditional matching in addition to semantic similarity?

### Why hybrid scoring?

### Why LangGraph?

### Why Telegram first?

### Why scheduler?

### Why not use an LLM for every decision?

### Where deterministic logic is preferred over LLM reasoning

This document is important because the goal is not only to build the project but to demonstrate engineering reasoning.

---

# 22. TEST RESULTS DOCUMENT

After actually executing the tests, produce a factual report.

Example:

```text
Total tests: XX
Passed: XX
Failed: XX
Skipped: XX

Integration tests:
PASS

E2E:
PASS

Database verification:
PASS

pgvector:
PASS

Telegram:
PASS / NOT VERIFIED

Scheduler:
PASS / NOT VERIFIED
```

Never fabricate results.

If something cannot be externally verified because credentials or infrastructure are unavailable, explicitly state:

```text
NOT VERIFIED — external dependency unavailable
```

rather than claiming success.

---

# 23. END-TO-END VERIFICATION REPORT

Produce a trace similar to:

```text
TEST RUN ID: XXXXX

Candidate Created
        ✓

CV Processed
        ✓

Profile Generated
        ✓

Profile Persisted
        ✓

Jobs Ingested
        ✓

Jobs Validated
        ✓

Duplicates Removed
        ✓

Embeddings Generated
        ✓

pgvector Retrieval
        ✓

Hybrid Matching
        ✓

Ranking
        ✓

LangGraph
        ✓

Notification Decision
        ✓

Telegram Notification
        ✓

Feedback
        ✓

Database Verification
        ✓
```

Include real timestamps/metrics where available.

---

# 24. PERFORMANCE METRICS

Capture useful metrics from the final E2E run.

For example:

```text
CV processing latency
Profile generation latency
Job ingestion latency
Embedding latency
Vector retrieval latency
Matching latency
LangGraph latency
Telegram latency
Total pipeline latency
```

Also capture:

```text
Jobs discovered
Jobs accepted
Jobs rejected
Jobs deduplicated
Jobs retrieved
Jobs finally recommended
Notifications sent
Feedback received
```

Do not optimize prematurely.

First establish a baseline.

---

# 25. DATA QUALITY VERIFICATION

Inspect real outputs.

Do not only check that code returned `200 OK`.

Check:

### Candidate profile

Is it actually correct?

### Job records

Are they valid?

### Embeddings

Are they present and correctly shaped?

### Recommendations

Are they actually relevant?

### Ranking

Does the ranking make sense?

### Notification

Does the user receive useful information?

The system must be **functionally correct**, not merely technically executable.

---

# 26. IDEMPOTENCY / DUPLICATION CHECK

Run the pipeline more than once where appropriate.

Verify:

```text
Same job
Same candidate
Same run
```

doesn't unnecessarily create duplicate jobs/recommendations/notifications.

If duplicate behavior is intentional, document it.

If not, fix it.

---

# 27. EMPTY / EDGE CASE TESTING

Test:

```text
No CV
Empty CV
No jobs
No relevant jobs
All jobs below threshold
Incomplete job
Incomplete candidate
Duplicate job
API failure
LLM timeout
Telegram failure
Database failure
```

The system must behave predictably.

---

# 28. DO NOT ADD UNNECESSARY FEATURES

Do NOT expand the MVP with:

- WhatsApp
- frontend dashboard
- AWS deployment
- advanced reinforcement learning
- sophisticated ML ranking models
- multiple messaging platforms
- huge scraping infrastructure
- unnecessary microservices

unless the current implementation absolutely requires them.

The objective is:

> **Finish and verify the existing MVP.**

Not restart development.

---

# 29. MANAGER DEMO PREPARATION

Prepare a concise manager demonstration.

The demo should show:

### 1. Problem

Traditional job searching requires manually searching hundreds of jobs.

### 2. Solution

An AI-powered personalized job discovery and recommendation agent.

### 3. Architecture

Show:

```text
Telegram
 ↓
FastAPI
 ↓
CV Intelligence
 ↓
Job Intelligence
 ↓
Semantic Search
 ↓
Hybrid Matching
 ↓
LangGraph
 ↓
Telegram
 ↓
Feedback
```

### 4. Live Flow

Upload CV → receive personalized recommendations.

### 5. Explain one recommendation

For example:

```text
Job A

Semantic relevance: X
Skill match: X
Experience match: X
Location match: X

Final score: X

Why recommended:
...
```

### 6. Show Agent

Explain what LangGraph actually controls.

### 7. Show Database

Demonstrate persistence.

### 8. Show Feedback

Demonstrate that the system learns/stores user feedback as designed.

### 9. Show Automation

Demonstrate scheduled execution.

### 10. Show Tests

Show actual test results.

---

# 30. FINAL QUALITY GATE

Before declaring Day 12 complete, verify ALL of the following:

```text
[ ] Project installs successfully
[ ] Environment configuration documented
[ ] Database starts successfully
[ ] Migrations work
[ ] FastAPI starts
[ ] Telegram works
[ ] CV extraction works
[ ] Candidate profile generation works
[ ] Candidate profile persists
[ ] Job ingestion works
[ ] Job validation works
[ ] Deduplication works
[ ] Embeddings work
[ ] pgvector retrieval works
[ ] Matching works
[ ] Scoring works
[ ] Ranking works
[ ] LangGraph works
[ ] Notification decision works
[ ] Telegram notification works
[ ] Feedback works
[ ] Feedback persists
[ ] Scheduler works
[ ] Error handling verified
[ ] Full test suite passes
[ ] Integration tests pass
[ ] E2E test passes
[ ] Database state verified
[ ] Security audit completed
[ ] Repository cleaned
[ ] README updated
[ ] Technical documentation complete
[ ] Manager demo prepared
```

---

# 31. IMPORTANT RULE — NEVER FAKE VERIFICATION

This is critical.

Never say:

```text
PASS
```

unless you actually executed and verified it.

Use:

```text
PASS
FAIL
PARTIAL
NOT VERIFIED
BLOCKED
```

with a reason.

Example:

```text
Telegram:
PARTIAL

Reason:
Unit tests pass, but live Telegram credentials were unavailable during verification.
```

This is much more valuable than pretending everything works.

---

# 32. WORK EXECUTION ORDER

Follow this exact order:

```text
PHASE 1
Repository Audit
        ↓
PHASE 2
Gap Analysis
        ↓
PHASE 3
Fix Critical Bugs
        ↓
PHASE 4
Complete Missing Integration
        ↓
PHASE 5
Run Unit Tests
        ↓
PHASE 6
Run Integration Tests
        ↓
PHASE 7
Run Real E2E Pipeline
        ↓
PHASE 8
Verify Database
        ↓
PHASE 9
Verify Telegram
        ↓
PHASE 10
Verify Scheduler
        ↓
PHASE 11
Security + Repository Cleanup
        ↓
PHASE 12
Documentation
        ↓
PHASE 13
Manager Demo Preparation
        ↓
PHASE 14
FINAL QUALITY GATE
```

---

# 33. REQUIRED FINAL RESPONSE FROM YOU

When all work is complete, provide a final engineering report with:

## A. Executive Summary

What was completed?

## B. Bugs Found

List every meaningful issue discovered.

For each:

```text
Issue
Root Cause
Fix
Verification
```

## C. Missing Components Completed

List everything that was previously incomplete.

## D. Tests

Provide actual results.

## E. E2E Verification

Provide the complete execution trace.

## F. Database Verification

Explain what was verified.

## G. Telegram Verification

Explain exactly what was verified.

## H. Scheduler Verification

Explain exactly what was verified.

## I. Security Audit

List security issues found and fixed.

## J. Documentation Created/Updated

List files.

## K. Known Limitations

Do not hide limitations.

## L. Final Architecture

Explain the final architecture.

## M. MVP Readiness

Give one of:

```text
READY
READY WITH MINOR LIMITATIONS
NOT READY
```

with justification.

---

# FINAL INSTRUCTION

Treat this as a **real software engineering completion task**, not a code-generation exercise.

You have permission to inspect the entire repository, modify existing code, add tests, add documentation, fix bugs, improve error handling, and restructure small parts where genuinely necessary.

But:

**Do not rewrite working components just for style.**

**Do not add unnecessary features.**

**Do not fabricate test results.**

**Do not claim an external integration is verified unless it was actually executed.**

**Do not stop after fixing the first bug.**

Continue until the project reaches the Day-12 quality gate or until a genuine external blocker prevents completion.

The final objective is:

> **A clean, reproducible, tested, end-to-end AI Job Hunting Agent MVP that I can confidently demonstrate to my manager and explain technically from architecture → AI/ML pipeline → agentic workflow → automation → user feedback.**

Start now with:

### STEP 1 — COMPLETE REPOSITORY AUDIT

Do not make major changes until you understand the current implementation and produce the gap analysis.
