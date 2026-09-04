"""Send a REAL Telegram notification to one user, on purpose, by hand.

    python -m scripts.send_test_notification --user-id 2
    python -m scripts.send_test_notification --user-id 2 --send
    python -m scripts.send_test_notification --user-id 2 --send --top 1

THIS SENDS A REAL MESSAGE TO A REAL PERSON. It is not a dry run, and it
is not called one -- `concurrent_claim_dryrun.py` is this repository's
example of a script whose name promised no writes and fired real Gemini
extractions instead, and the rule that broke is worth stating: "dryrun"
in a script name is a promise. This name makes no promise to keep.

Without --send it prints and exits, because the default behaviour of a
script that messages people should be to show you what it would do.

WHY THIS EXISTS AT ALL

Because the production gate passes nothing today. `weight_covered` is
0.50 on 242 of 294 pairs against a floor of 0.55, so `notify_eligible`
is 0 and the delivery node cannot execute on real data. That is the
gate working as designed (CLAUDE.md section 1) and it must not be
"fixed" by lowering the floor.

But it leaves the delivery path unproven, and "implemented" is not
"works". This script proves delivery INDEPENDENTLY of the gate: it
selects rows by rank instead, and hands them to exactly the same
`deliver_notifications()` the scheduled run uses. So a message arriving
here proves the formatter, the attempt row, the send, the status
update, the buttons and the feedback round trip -- everything except
the selection, which the probe already measures on its own.

WHY IT IS A SEPARATE SCRIPT AND NOT `run_agent.py --force`

Because a bypass flag on the production path is a bypass flag on the
production path forever. `deliver_notifications()` takes an
already-chosen list and has no gate, no `force` parameter and no way to
ask whether a row qualified. The two callers differ in which list they
build:

    production:  recommendations -> gate -> deliver
    this script: recommendations -> rank -> a person reads the gate
                 report -> --send -> deliver

There is no configuration value that makes the scheduled run behave
like this one. That is the safety property, and it is why
`notify_ignore_gate` was not added.

EVERY ROW IT SENDS IS MARKED trigger_source = 'manual_test'

Without that column, the first message sent from here would make "has
the production gate ever actually fired?" unanswerable for the life of
the table.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

if sys.platform == "win32":
    # psycopg's async driver cannot use the ProactorEventLoop Windows
    # defaults to. Set BEFORE anything imports the database layer.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# The notification body contains emoji, and this script PRINTS it. The
# Windows console is cp1252, which cannot encode U+1F525, so printing
# the preview raised UnicodeEncodeError and killed the script before it
# reached the send -- observed, not anticipated.
#
# Only the console is affected. The message itself is a Python str sent
# to Telegram as JSON over HTTPS, so what a terminal can render has
# nothing to do with what a user receives.
#
# errors="replace" rather than forcing UTF-8: this keeps whatever the
# terminal actually supports, so a UTF-8 console still shows the emoji
# and cp1252 degrades to "?" instead of crashing. Forcing UTF-8 would
# trade a crash for mojibake on exactly the console that cannot read it.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

from app.core.config import settings  # noqa: E402
from app.db.models.recommendation import TRIGGER_SOURCE_MANUAL_TEST  # noqa: E402
from app.db.session import dispose_engine, init_engine  # noqa: E402
from app.services.notification_delivery import (  # noqa: E402
    GateEvaluation,
    deliver_notifications,
    evaluate_candidates,
)


def _print_gate_report(evaluations: list[GateEvaluation]) -> None:
    """Every row, with the gate verdict and the margin on each failure.

    The margins are the useful part. "fails coverage" invites lowering
    the floor; "fails coverage by 0.0500", on every row, says the whole
    population sits at one value and the floor is a step function rather
    than a dial -- which is a different conversation, and the one Day 10
    recorded as needing a decision rather than a patch.
    """
    print("--- gate report")
    print(
        f"thresholds: notification_threshold per user, "
        f"semantic_notify_floor {settings.semantic_notify_floor}, "
        f"min_weight_covered_to_notify {settings.min_weight_covered_to_notify}"
    )

    if not evaluations:
        print("  (no recommendations for this user, or the user is inactive)")
        return

    for index, evaluation in enumerate(evaluations, start=1):
        candidate = evaluation.candidate
        verdict = "ELIGIBLE" if evaluation.eligible else "blocked"
        if evaluation.already_sent:
            verdict += " (already sent)"
        print(
            f"  [{index}] job {candidate.job_id}  "
            f"score {candidate.final_score:.4f}  "
            f"coverage {candidate.weight_covered:.2f}  {verdict}"
        )
        for reason in evaluation.failing_gates():
            print(f"        fails: {reason}")


def _print_result(result: dict) -> None:
    print("--- delivery result")
    for key in (
        "status",
        "trigger_source",
        "eligible_selected",
        "attempted",
        "sent",
        "failed",
        "skipped_duplicate",
        "users_deactivated",
    ):
        print(f"{key:22} {result.get(key)}")


async def run(args: argparse.Namespace) -> int:
    evaluations = await evaluate_candidates(user_id=args.user_id)

    print(f"user_id                {args.user_id}")
    print(f"trigger_source         {TRIGGER_SOURCE_MANUAL_TEST}")
    print(f"recommendations found  {len(evaluations)}")
    _print_gate_report(evaluations)

    if not evaluations:
        print(
            "\nNothing to send. Either this user has no recommendations, "
            "or they are inactive."
        )
        return 1

    # Ranked, NOT gated. This is the line that makes the script a test of
    # delivery rather than a second implementation of the gate: the top N
    # by score, whatever the gate thinks of them.
    #
    # Already-sent rows are still excluded, because the partial unique
    # index would refuse the second SENT row anyway and the useful
    # outcome of a manual test is a message arriving, not a duplicate
    # being correctly rejected -- which the integration tests cover
    # deliberately and this would cover by accident.
    chosen = [
        evaluation.candidate
        for evaluation in evaluations
        if not evaluation.already_sent
    ][: args.top]

    if not chosen:
        print("\nEvery recommendation for this user has already been sent.")
        return 1

    print("\n--- would send")
    for candidate in chosen:
        print(f"  job {candidate.job_id}  score {candidate.final_score:.4f}")
        print("  " + "-" * 60)
        for line in candidate.reply.text.splitlines():
            print(f"  | {line}")
        print("  " + "-" * 60)
        labels = [button.label for row in candidate.reply.buttons for button in row]
        print(f"  buttons: {', '.join(labels)}")

    if not args.send:
        print(
            f"\nNOT SENT. This was a preview.\n"
            f"Re-run with --send to deliver {len(chosen)} real Telegram "
            f"message(s) to user {args.user_id}."
        )
        return 0

    if not settings.telegram_bot_token:
        print("\nTELEGRAM_BOT_TOKEN is not configured.")
        return 1

    print(f"\nSENDING {len(chosen)} real message(s)...")

    result = await deliver_notifications(
        chosen,
        trigger_source=TRIGGER_SOURCE_MANUAL_TEST,
        dry_run=False,
    )
    _print_result(result)

    # Non-zero unless every message landed. A manual test that half
    # worked must not exit 0.
    return 0 if result.get("status") == "complete" else 1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send a REAL Telegram notification to one user.",
    )
    # Required, and deliberately not defaulted to "everyone". A script
    # that sends real messages should not have a default that sends them
    # to the whole table.
    parser.add_argument(
        "--user-id",
        type=int,
        required=True,
        help="REQUIRED. The internal user id (not the telegram id).",
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="Actually deliver. Without this the script only previews.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=1,
        help="How many of the top-ranked recommendations to send (default 1).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    if init_engine() is None:
        print("DATABASE_URL is not configured.")
        raise SystemExit(1)

    try:
        exit_code = asyncio.run(run(args))
    finally:
        asyncio.run(dispose_engine())

    raise SystemExit(exit_code)
