"""Windows-safe entry point for the FastAPI application.

Start the app with `python run.py` rather than `uvicorn app.main:app`.

Why this file exists at all, given that uvicorn already has a CLI:
psycopg's async driver cannot run on the ProactorEventLoop that
Windows uses by default, so `uvicorn app.main:app` crashes at the
first database call. `uvicorn app.main:app --reload` appears to work
only by accident -- reload mode imports the application in a
subprocess before an event loop exists -- but it pays for that by
killing in-flight background tasks every time a file is saved,
stranding whatever work they had claimed.

The ordering below is the whole point of the file and is fragile in a
way that is not obvious from reading it. asyncio's event loop policy
must be replaced BEFORE uvicorn is imported, because importing uvicorn
is enough to pull in modules that read the current policy. That is why
`import uvicorn` sits below the policy call instead of at the top of
the file with the other imports, and why moving it back up would
silently reintroduce the crash this file exists to prevent.
"""

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Imported after the policy is set on purpose. See the module docstring.
import uvicorn  # noqa: E402

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        # No reload. Reload is what kills background tasks mid-flight;
        # avoiding it is half the reason this file exists. Use the
        # uvicorn CLI directly if you genuinely want reload and have no
        # background work running.
        reload=False,
        log_level="info",
    )
