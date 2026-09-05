"""Refuse an archive that must not be shared. Read-only, no writes.

    python -m scripts.verify_archive C:\\Temp\\handoff.zip
    python -m scripts.verify_archive --self-test

WHY THIS EXISTS

`scripts/pack.ps1` guarantees its own output and nothing else. Every
archive incident in this project's history -- three of them now -- was
a person reaching for Explorer's "Compress to zip" or
`Compress-Archive -Path .` instead of the script, and nothing in the
repository can intercept a right-click. This was named but not built
in the Day 10 Part 3 record; the fourth incident happened before it
got written.

The most recent one, the archive this Day 12 work arrived in, carried
`.env`, the whole of `storage/cvs/` (21 real candidate CV PDFs) and
`logs/`. Three of the five forbidden patterns in one file. `pack.ps1`
was not broken, it was bypassed.

So this takes a path to ANY zip and answers one question: would
sharing it leak something. It never modifies the archive and never
prints the contents of a matched file -- only its name, which is the
whole point.

WHY THE PATTERNS LIVE HERE AND NOT ONLY IN pack.ps1

Two copies of a safety list drift, and the copy that drifts is the one
nobody runs. `tests/test_verify_archive.py` therefore parses the labels
out of `scripts/pack.ps1` and fails when the two disagree, so adding a
pattern in one place and forgetting the other is a test failure rather
than a silent gap. That is a drift DETECTOR rather than shared code,
chosen because rewriting pack.ps1 to import from Python would put a
proven, incident-hardened script at risk to save a duplicated list.

EXIT CODES

    0  clean
    1  forbidden entries found
    2  could not read the archive at all

1 and 2 are separated deliberately. "This archive is unsafe" and "I
could not tell" are different answers, and a wrapper that treats any
non-zero as unsafe still behaves correctly, while one that treats any
non-one as safe does not.
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory


@dataclass(frozen=True)
class ForbiddenPattern:
    """One thing that must never be inside a shared archive."""

    label: str
    why: str
    matches: Callable[[str], bool]


def _is_env(name: str) -> bool:
    # `.env` exactly, or `.env` at the end of any directory path.
    # NOT `.env.example`, which is meant to be shared, and not
    # `something.env`, which is a different file.
    return name == ".env" or name.endswith("/.env")


def _in_directory(directory: str) -> Callable[[str], bool]:
    def matches(name: str) -> bool:
        return name.startswith(f"{directory}/") or f"/{directory}/" in name

    return matches


# Mirrors $ForbiddenPatterns in scripts/pack.ps1. Kept in the same
# order so a human comparing the two files can do it line by line.
FORBIDDEN_PATTERNS: tuple[ForbiddenPattern, ...] = (
    ForbiddenPattern(
        label=".env",
        why="five live credentials",
        matches=_is_env,
    ),
    ForbiddenPattern(
        label=".git/",
        why="full history, including anything ever committed by mistake",
        matches=_in_directory(".git"),
    ),
    ForbiddenPattern(
        label="storage/",
        why="real candidate CVs -- a key can be rotated, a CV cannot be recalled",
        matches=_in_directory("storage"),
    ),
    ForbiddenPattern(
        label="*.zip",
        why="a nested archive is not inspected by this check",
        matches=lambda name: name.endswith(".zip"),
    ),
    ForbiddenPattern(
        label="*.pdf",
        why="uploaded CVs are PDFs",
        matches=lambda name: name.endswith(".pdf"),
    ),
)


@dataclass(frozen=True)
class Violation:
    label: str
    why: str
    entries: tuple[str, ...]


def entry_names(archive_path: Path) -> list[str]:
    """Every entry in the zip. Raises for anything unreadable."""
    with zipfile.ZipFile(archive_path) as archive:
        return archive.namelist()


def find_violations(names: Iterable[str]) -> list[Violation]:
    """Which forbidden patterns this list of entries matches.

    Takes names rather than a path so the rules can be tested against
    a list, with no zip on disk. Every matching entry is reported, not
    just the first: "storage/cvs/2/a.pdf and 20 others" is a different
    conversation from one stray file.
    """
    violations: list[Violation] = []
    for pattern in FORBIDDEN_PATTERNS:
        hits = tuple(sorted(name for name in names if pattern.matches(name)))
        if hits:
            violations.append(
                Violation(label=pattern.label, why=pattern.why, entries=hits)
            )
    return violations


def _report(archive_path: Path, names: list[str], violations: list[Violation]) -> None:
    file_count = len([name for name in names if not name.endswith("/")])

    print(f"archive            {archive_path}")
    print(f"entries            {file_count}")

    if not violations:
        print("result             CLEAN")
        print("")
        print("No forbidden entry found. This says nothing about whether the")
        print("archive contains what it SHOULD -- see CLAUDE.md section 3 on")
        print("untracked files being invisible to git archive.")
        return

    print("result             FORBIDDEN ENTRIES FOUND")
    print("")
    for violation in violations:
        print(f"  {violation.label}  ({violation.why})")
        for entry in violation.entries[:10]:
            print(f"      {entry}")
        if len(violation.entries) > 10:
            print(f"      ... and {len(violation.entries) - 10} more")
    print("")
    print("DO NOT SHARE THIS FILE. Rebuild it with:")
    print("    powershell -File scripts/pack.ps1")


def _self_test() -> int:
    """Build archives that SHOULD fail, and prove they do.

    A verifier with nothing checking it is the same shape of problem as
    a packer with nothing checking it: it passes everything, silently,
    the moment a pattern stops matching. So this constructs one archive
    per pattern, plus a clean one, and asserts each verdict.

    Writes only into a temporary directory, which is removed on the way
    out. It is a self-test rather than a unit test as well as one --
    `tests/test_verify_archive.py` covers the same rules -- because the
    person about to share an archive at 6pm runs scripts, not pytest.
    """
    cases: list[tuple[str, list[str], bool]] = [
        ("clean repository", ["app/main.py", "README.md", ".env.example"], True),
        ("root .env", ["app/main.py", ".env"], False),
        ("nested .env", ["AI_JOB_HUNT_AGENT/.env"], False),
        ("git directory", ["AI_JOB_HUNT_AGENT/.git/config"], False),
        ("storage", ["AI_JOB_HUNT_AGENT/storage/cvs/2/a.bin"], False),
        ("nested zip", ["files (1).zip"], False),
        ("a CV pdf", ["docs/cv.pdf"], False),
        ("logs are not forbidden", ["logs/run.log"], True),
    ]

    failures = 0

    with TemporaryDirectory() as directory:
        for name, entries, expected_clean in cases:
            path = Path(directory) / "case.zip"
            with zipfile.ZipFile(path, "w") as archive:
                for entry in entries:
                    archive.writestr(entry, "x")

            violations = find_violations(entry_names(path))
            actually_clean = not violations
            ok = actually_clean == expected_clean
            failures += 0 if ok else 1

            verdict = "clean" if actually_clean else "rejected"
            expected = "clean" if expected_clean else "rejected"
            status = "ok  " if ok else "FAIL"
            print(f"{status} {name:<28} expected {expected:<8} got {verdict}")

            path.unlink()

    print("")
    if failures:
        print(f"self-test result   FAIL ({failures} case(s))")
        return 1

    print("self-test result   PASS")
    print("")
    print("Note what this does NOT prove: that `logs/` is safe to share.")
    print("It is not on the forbidden list because pack.ps1 does not list")
    print("it either, and the two lists are kept identical on purpose.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.verify_archive",
        description=(
            "Refuse an archive containing credentials, CVs or git history. "
            "Read-only."
        ),
    )
    parser.add_argument(
        "archive",
        nargs="?",
        help="Path to the .zip to inspect.",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Prove the rules catch what they are supposed to, then exit.",
    )

    arguments = parser.parse_args(argv)

    if arguments.self_test:
        return _self_test()

    if not arguments.archive:
        parser.error("give a path to an archive, or --self-test")

    archive_path = Path(arguments.archive)

    try:
        names = entry_names(archive_path)
    except FileNotFoundError:
        print(f"cannot read        {archive_path} does not exist", file=sys.stderr)
        return 2
    except (zipfile.BadZipFile, OSError) as error:
        # Exit 2, not 1. An unreadable archive has not been cleared and
        # has not been condemned, and saying otherwise in either
        # direction would be a claim this script cannot support.
        print(f"cannot read        {archive_path}: {error}", file=sys.stderr)
        return 2

    violations = find_violations(names)
    _report(archive_path, names, violations)

    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
