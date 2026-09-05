"""Windows-safe entry point for the FastAPI application.

Start the app with `python run.py` rather than `uvicorn app.main:app`.

Why this file exists at all, given that uvicorn already has a CLI:
psycopg's async driver cannot run on the ProactorEventLoop that
Windows uses by default, so `uvicorn app.main:app` crashes at the
first database call.

HOW THIS FILE USED TO WORK, AND WHY THAT STOPPED

Until Day 12 this file called
`asyncio.set_event_loop_policy(WindowsSelectorEventLoopPolicy())`
before importing uvicorn, and relied on `uvicorn.run()` reading that
policy when it built its loop.

uvicorn 0.36.0 replaced `Config.setup_event_loop` with
`Config.get_loop_factory`, and `Server.run` now calls

    asyncio.run(self.serve(...), loop_factory=self.config.get_loop_factory())

`uvicorn/loops/asyncio.py` returns `asyncio.ProactorEventLoop` on
win32 whenever `use_subprocess` is false. **An explicit loop_factory
bypasses the event loop policy entirely**, so the policy call became
dead code -- silently. The API started, the bot connected, `/start`
was received, and the first database call died with
`psycopg.InterfaceError: Psycopg cannot use the 'ProactorEventLoop'`.
Every log line before that said the service was healthy.

Setting the policy is therefore no longer enough, and there is no
uvicorn setting that asks for a SelectorEventLoop on Windows --
`loop="asyncio"` reaches the same branch. The only supported way to
choose the loop is to pass a factory ourselves, which means
constructing Config and Server here instead of calling `uvicorn.run`.

The policy call below is KEPT even though uvicorn no longer reads it:
anything else in the process that creates a loop without a factory --
a library, a debugger, a future entry point -- still consults it.

WHY NOT --reload

Reload mode sets `use_subprocess`, which makes uvicorn choose the
SelectorEventLoop by itself. That is why `uvicorn app.main:app
--reload` appeared to work while plain `uvicorn app.main:app` did not.
It pays for that by killing in-flight background tasks on every file
save, stranding whatever work they had claimed.
"""

import asyncio
import sys

if sys.platform == "win32":
    # No longer read by uvicorn. Kept for every other loop in this
    # process; see the module docstring.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Imported after the policy is set. Harmless now that the loop is
# chosen explicitly below, but importing uvicorn pulls in modules that
# read the current policy, and the ordering costs nothing to keep.
import uvicorn  # noqa: E402

HOST = "127.0.0.1"
PORT = 8000


def loop_factory():
    """The loop psycopg can actually use.

    `asyncio.SelectorEventLoop` is the correct class on both platforms:
    on Windows it is `ProactorEventLoop`'s alternative, and on Linux
    and macOS it is already the default, so this is not a
    Windows-only branch pretending to be portable.
    """
    return asyncio.SelectorEventLoop()


if __name__ == "__main__":
    config = uvicorn.Config(
        "app.main:app",
        host=HOST,
        port=PORT,
        # No reload. Reload is what kills background tasks mid-flight;
        # avoiding it is half the reason this file exists.
        reload=False,
        log_level="info",
    )
    server = uvicorn.Server(config)

    # asyncio.run's loop_factory parameter is Python 3.12+. This
    # project pins 3.12, and an older interpreter would fail here
    # loudly rather than silently running on the wrong loop -- which
    # is the failure mode this file exists to prevent.
    asyncio.run(server.serve(), loop_factory=loop_factory)
