"""The shipped app writes to its log, not to a console it does not have.

Two `print("DEBUG: ...")` calls sat in the entry point of a windowed build,
where there is no stdout to receive them — they survived to a release because
nothing looked. This reads the source rather than running anything, because the
lines it is looking for run at startup, before any test gets a chance.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

APP = pathlib.Path(__file__).resolve().parent.parent / "app"

#: `python -m app.main --help`-style scripts are allowed to talk to a terminal.
#: Nothing in this package is one, so the list is empty and stays that way until
#: something genuinely is.
SPEAKS_TO_A_TERMINAL: set[str] = set()


def prints_in(source: str) -> list[tuple[int, str]]:
    """Every call to the builtin `print`, with the line and what it starts with."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "print"):
            continue
        first = node.args[0] if node.args else None
        text = first.value if isinstance(first, ast.Constant) and isinstance(first.value, str) else ""
        found.append((node.lineno, text))
    return found


@pytest.mark.parametrize("path", sorted(APP.rglob("*.py")), ids=lambda p: p.name)
def test_no_module_prints_to_a_console_that_may_not_exist(path):
    if path.stem in SPEAKS_TO_A_TERMINAL:
        pytest.skip(f"{path.name} is a command-line entry point")

    offenders = prints_in(path.read_text(encoding="utf-8"))

    assert not offenders, (
        f"{path.name} calls print at line(s) {[line for line, _ in offenders]}: "
        f"{[text[:40] for _, text in offenders]}. A windowed build has no stdout — "
        f"use the logger."
    )


def test_the_scan_can_actually_see_a_print():
    """Guard against the check passing because the parser stopped finding
    anything — the failure mode that would make every test above vacuous."""
    assert prints_in('print("DEBUG: creating overlay", flush=True)') == [(1, "DEBUG: creating overlay")]
    assert prints_in("import os\nos.getcwd()\n") == []
