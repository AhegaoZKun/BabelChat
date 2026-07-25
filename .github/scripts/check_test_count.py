"""Fail CI when the test suite shrinks.

A green `pytest` says every test that ran passed. It says nothing about tests
that stopped existing. In July 2026 a snapshot import (d9f8f9f) deleted six
test files — 124 tests — and CI stayed green for ten days because 28 survivors
still passed.

This guard closes that gap. It reads the count from the junit report of the
run that just happened, so it costs no second collection pass, and it compares
against a floor that only ever moves up by hand:

    adding tests    -> count rises  -> always passes
    removing tests  -> count drops  -> fails until someone lowers MIN_TESTS

Lowering the floor stays possible — it just stops being silent.

Usage: python .github/scripts/check_test_count.py <junit-xml> <min-tests>
"""

from __future__ import annotations

import sys
from pathlib import Path
from xml.etree import ElementTree


def collected_tests(report: Path) -> int:
    """Total tests recorded in a junit XML report.

    Reads the `tests` attribute rather than pytest's console output: the
    human-readable summary changes between pytest versions, the junit
    attribute does not.
    """
    root = ElementTree.parse(report).getroot()
    suites = [root] if root.tag == "testsuite" else root.iter("testsuite")
    return sum(int(suite.get("tests", 0)) for suite in suites)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(f"usage: {Path(argv[0]).name} <junit-xml> <min-tests>", file=sys.stderr)
        return 2

    report = Path(argv[1])
    floor = int(argv[2])

    # A missing or malformed report means we do not know the count. Treat that
    # as a failure: the whole point is to refuse to guess.
    if not report.is_file():
        print(f"ERROR: junit report not found: {report}", file=sys.stderr)
        return 1
    try:
        count = collected_tests(report)
    except (ElementTree.ParseError, ValueError) as exc:
        print(f"ERROR: cannot read test count from {report}: {exc}", file=sys.stderr)
        return 1

    if count < floor:
        # ASCII only: this runs on windows-latest, where stderr may be cp1252
        # and a stray em-dash would turn a clear diagnosis into mojibake.
        print(
            f"ERROR: test suite shrank - {count} tests collected, expected at least {floor}.\n"
            f"       {floor - count} test(s) went missing. If tests were removed on purpose,\n"
            f"       lower MIN_TESTS in .github/workflows/ci.yml in the same commit and say why.",
            file=sys.stderr,
        )
        return 1

    print(f"OK: {count} tests collected (floor {floor}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
