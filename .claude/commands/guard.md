---
description: Check a proposed change against the do-not-fix list before writing any code
---

Before writing any code for the change described below, work through
this checklist and report on each point. Do not skip to the
implementation.

Change under consideration: $ARGUMENTS

1. **Is this on the do-not-fix list?** Read section 1 of `CLAUDE.md`.
   State explicitly whether the change touches any row in that table.
   Remember that the list is a known-incomplete reconstruction: also
   search `docs/` for the words "deliberate", "on purpose", "not a
   bug", and "by design" near anything this change would touch, and
   report what you find.

2. **What would this look like if it silently did nothing?** Name the
   specific status, count or score that would still look healthy.

3. **Which stage does the existing check actually observe?** If this
   change adds or modifies an assertion, say whether it observes the
   plan (counts read before the loop) or the work (what the loop
   actually produced). Both kinds exist here; only the second would
   have caught the Day 8 limit bug.

4. **Is there a cheaper check that predicts the outcome?** A regex over
   stored text, a single query, a pure-function test at a boundary. If
   one exists, propose it instead and stop.

5. **Boundaries.** Does this introduce or change a comparison? If so,
   state what happens at exactly the boundary value, and whether the
   test covers that exact value or merely a value near it.

6. **Secrets.** Does this add logging of a URL, request, response body
   or exception originating in `app/integrations/`? Adzuna's
   credentials are query parameters. Say so explicitly, either way.

7. **Layering.** Which layer does the new code live in, and does it
   respect section 4 of `CLAUDE.md`?

Then stop and wait. Report your answers and the proposed approach
before writing anything.