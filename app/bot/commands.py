"""The single list of user-facing commands.

Three things read this and none of them keep their own copy:
`register_handlers()` (`app/bot/handlers/__init__.py`) uses these names
to decide which `CommandHandler` to add, `/help`'s text
(`app/bot/handlers/common.py`) is built from these descriptions, and
`app/main.py`'s startup calls `set_my_commands` with them so Telegram's
own "/" menu shows the same list. Two hand-maintained copies of a
command list are a name that means one of them is asked less often —
this repository does not have a second copy for that name to belong to.

No handler references here on purpose. `common.py` is itself one of
the modules `register_handlers()` imports handlers from; if this file
imported handler callables, `common.py` importing this file back would
be circular. `tests/test_bot_commands.py` closes the loop the other
way: it asserts the commands actually registered on a real
`Application` match this list's names exactly, in both directions.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommandInfo:
    name: str
    description: str


COMMANDS: tuple[CommandInfo, ...] = (
    CommandInfo("start", "Begin onboarding, or resume where you left off"),
    CommandInfo(
        "status", "Check what's on file — CV, preferences, and setup progress"
    ),
    CommandInfo("profile", "See what I understood from your CV"),
    CommandInfo("update_cv", "Replace your CV with a new one"),
    CommandInfo(
        "preferences",
        "Change one preference — roles, locations, experience, or alert threshold",
    ),
    CommandInfo("restart", "Redo setup from the beginning"),
    CommandInfo("help", "Show this list of commands again"),
    CommandInfo("ping", "Check that I'm alive and responding"),
)
