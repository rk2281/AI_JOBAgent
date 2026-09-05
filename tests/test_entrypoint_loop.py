"""run.py must not hand psycopg a ProactorEventLoop.

WHY THIS FILE EXISTS

`run.py` used to set `WindowsSelectorEventLoopPolicy` and trust
`uvicorn.run()` to read it. uvicorn 0.36.0 replaced
`Config.setup_event_loop` with `Config.get_loop_factory` and now calls
`asyncio.run(..., loop_factory=...)`, and an explicit loop_factory
bypasses the policy. On Windows the factory is `ProactorEventLoop`,
which psycopg's async driver cannot use.

Nothing caught it. `tests/test_health.py` drives the app through
`TestClient`, which runs its own loop via anyio and never touches
uvicorn's loop selection at all -- so the API "passed" while
`python run.py` could not serve a single database-backed request. The
service started, logged "Application startup complete", accepted
/start, and died on the first query.

These are structural checks over the source and over uvicorn's own
behaviour. They cannot start a server, and they are not a substitute
for running `python run.py` on Windows once.
"""

from __future__ import annotations

import ast
import asyncio
import sys
from pathlib import Path

RUN_PY = Path(__file__).resolve().parent.parent / "run.py"


def test_run_py_passes_an_explicit_loop_factory() -> None:
    """The policy alone is not enough any more.

    If this fails because somebody simplified run.py back to
    `uvicorn.run(...)`, the symptom in production is not a crash at
    startup -- it is a healthy-looking service that dies on its first
    database call.
    """
    # Parsed, not grepped. The docstring in run.py explains the whole
    # problem and therefore mentions `uvicorn.run()` in prose -- a
    # substring check on the source flags that as the call itself,
    # which is a test failing on its own documentation. Observed while
    # writing this file.
    tree = ast.parse(RUN_PY.read_text(encoding="utf-8"))

    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]

    def is_attribute_call(node: ast.Call, owner: str, name: str) -> bool:
        func = node.func
        return (
            isinstance(func, ast.Attribute)
            and func.attr == name
            and isinstance(func.value, ast.Name)
            and func.value.id == owner
        )

    assert not any(is_attribute_call(call, "uvicorn", "run") for call in calls), (
        "run.py calls uvicorn.run(), which selects its own loop factory "
        "and cannot be told to use a SelectorEventLoop on Windows."
    )

    asyncio_runs = [call for call in calls if is_attribute_call(call, "asyncio", "run")]
    assert asyncio_runs, "run.py does not call asyncio.run()"
    assert any(
        keyword.arg == "loop_factory"
        for call in asyncio_runs
        for keyword in call.keywords
    ), (
        "run.py no longer passes a loop_factory. uvicorn >= 0.36 chooses "
        "ProactorEventLoop on Windows and ignores the event loop policy, "
        "so psycopg will fail on the first query."
    )


def test_uvicorn_still_chooses_proactor_on_windows() -> None:
    """Pins the reason run.py is shaped the way it is.

    If a future uvicorn stops returning ProactorEventLoop on win32,
    this fails and run.py can be simplified. Until then, the failure
    of this test is the only thing that would tell anyone the
    workaround had become unnecessary -- or that it had become
    insufficient in some new way.
    """
    from uvicorn.loops.asyncio import asyncio_loop_factory

    assert asyncio_loop_factory(use_subprocess=False) is asyncio.ProactorEventLoop \
        if sys.platform == "win32" else True

    # And the reload path picks a different one, which is why
    # `uvicorn app.main:app --reload` appeared to work.
    assert asyncio_loop_factory(use_subprocess=True) is asyncio.SelectorEventLoop


def test_the_chosen_loop_is_one_psycopg_accepts() -> None:
    """SelectorEventLoop on both platforms, not a Windows-only branch."""
    import run

    loop = run.loop_factory()
    try:
        assert isinstance(loop, asyncio.SelectorEventLoop)
        assert not isinstance(loop, getattr(asyncio, "ProactorEventLoop", ()))
    finally:
        loop.close()
