"""The CI workflow has to build what the tests assume is there.

`test_the_shipped_library_declares_the_scanner_entry_point` says a missing
scanner is a packaging bug. It was right, and the packaging bug was in CI
itself: the library is a build artifact, deliberately untracked, and nothing in
the workflow produced one — so that test failed on every run, and had done for
long enough that a red CI had stopped meaning anything.

This file is what fails if the build step is dropped again, instead of the
native-scanner test failing for a reason that has nothing to do with the
scanner.
"""

from __future__ import annotations

import pathlib

import pytest

yaml = pytest.importorskip("yaml", reason="PyYAML parses the workflow")

WORKFLOW = pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"


@pytest.fixture(scope="module")
def steps() -> list[dict]:
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return document["jobs"]["lint-and-test"]["steps"]


def named(steps: list[dict], fragment: str) -> int:
    """Index of the first step whose name contains `fragment`, or -1."""
    for index, step in enumerate(steps):
        if fragment.lower() in (step.get("name") or "").lower():
            return index
    return -1


def test_the_native_scanner_is_built_before_the_tests_run(steps):
    build = named(steps, "native scanner")
    test = named(steps, "test")

    assert build >= 0, "nothing in CI builds the scanner, so the test for it cannot pass"
    assert test >= 0, "the workflow no longer runs the tests"
    assert build < test, "the scanner is built after the tests that need it"


def test_the_build_step_actually_invokes_cargo(steps):
    """A step named for the scanner that does not compile it would satisfy the
    test above and nothing else."""
    build = steps[named(steps, "native scanner")]

    assert "cargo build" in build["run"]
    assert "--release" in build["run"], "the loader looks for the release build"


def test_the_built_library_is_put_where_the_loader_looks(steps):
    """`candidate_paths` searches the app directory and its parent — not the
    crate's target directory, which is where cargo leaves it."""
    from app.native_scanner import candidate_paths, library_name

    build = steps[named(steps, "native scanner")]
    destinations = {path.parent.name for path in candidate_paths(library_name())}

    assert "Copy-Item" in build["run"], "the library is never moved out of target/release"
    assert any(f"{name}/" in build["run"] or f"{name}\\" in build["run"] for name in destinations), (
        f"the copy destination is not one of the places the loader searches: {destinations}"
    )


def test_a_rust_warning_fails_the_build(steps):
    """Nothing compiled this crate for months, and an unused import sat in it
    the whole time. A warning nobody is shown is a warning nobody fixes."""
    build = steps[named(steps, "native scanner")]

    assert build.get("env", {}).get("RUSTFLAGS") == "-D warnings"


def declared_test_functions() -> int:
    """Test functions defined in this suite — a genuine lower bound, since
    parametrisation only ever multiplies them."""
    import ast

    total = 0
    for path in pathlib.Path(__file__).resolve().parent.glob("test_*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        total += sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("test_")
        )
    return total


def test_the_test_count_guard_is_not_set_below_the_suite(steps):
    """The ratchet had been set from a developer machine's figure while CI
    collected fifty fewer tests, so it could not pass however green the suite
    was — a number that can never be met is not a gate, it is noise. The other
    direction is the one arithmetic can check: a ratchet quietly lowered to get
    a red build green stops guarding anything at all."""
    guard = steps[named(steps, "guard test count")]
    declared = int(guard["env"]["MIN_TESTS"])
    functions = declared_test_functions()

    assert functions > 100, f"the scan found only {functions} test functions — it has stopped working"
    assert declared >= functions, (
        f"MIN_TESTS={declared} is below the {functions} test functions this suite defines, "
        f"so tests could be deleted without the ratchet noticing"
    )
