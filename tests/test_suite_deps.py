"""Every optional import the suite guards must actually be installed.

`pytest.importorskip` is the right tool for a dependency that genuinely may be
absent — `gi` is, on Windows. It is the wrong outcome for one that is merely
missing from `requirements.txt`: the tests turn into skips, the run stays
green, and nobody is told.

That is not hypothetical. `PyYAML` was never declared, so the 27 tests that
assert on the release workflow ran only on a developer machine and skipped on
CI — the one place the workflow they check actually runs. `cryptography` had
the same hole, and with it the check that the bundled trust anchor has not
expired and is still the certificate that was reviewed.

So: each guarded module is either installed here, or named below with the
reason it cannot be. A new `importorskip` for an undeclared package fails this
file rather than quietly shrinking the suite.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
from importlib.metadata import packages_distributions

import pytest

TESTS = pathlib.Path(__file__).resolve().parent
REQUIREMENTS = TESTS.parent / "requirements.txt"

#: Modules that legitimately may be absent, and why. Anything else guarded by
#: `importorskip` has to be a declared dependency.
ALLOWED_ABSENT = {
    "gi": "PyGObject is the GTK frontend's binding — Linux only, and CI runs on Windows",
}

GUARD = re.compile(r"""importorskip\(\s*["']([A-Za-z0-9_.]+)["']""")


def guarded_modules() -> dict[str, set[str]]:
    """Every module name passed to importorskip, and where it was asked for."""
    found: dict[str, set[str]] = {}
    for path in sorted(TESTS.glob("test_*.py")):
        for module in GUARD.findall(path.read_text(encoding="utf-8")):
            found.setdefault(module, set()).add(path.name)
    return found


def test_the_scan_finds_the_guards_that_are_there():
    """A regex that matched nothing would make every test below vacuous, and it
    would look exactly like a clean suite."""
    found = guarded_modules()

    assert len(found) >= 4, f"the scan found only {found} — the pattern has stopped matching"
    assert "lupa" in found, "the Lua runtime guard is in seven files and must be seen"


@pytest.mark.parametrize("module", sorted(guarded_modules()))
def test_a_guarded_import_is_installed_or_explained(module):
    package = module.split(".")[0]
    if package in ALLOWED_ABSENT:
        pytest.skip(ALLOWED_ABSENT[package])

    where = ", ".join(sorted(guarded_modules()[module]))
    assert importlib.util.find_spec(package) is not None, (
        f"{where} skips itself without {package}, and {package} is not installed here. "
        f"Declare it in requirements.txt, or add it to ALLOWED_ABSENT with the reason."
    )


def declared_distributions() -> set[str]:
    """Distribution names in requirements.txt, normalised the way pip is."""
    names = set()
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        name = re.split(r"[<>=!;\[ ]", line, maxsplit=1)[0]
        if name:
            names.add(name.lower().replace("_", "-"))
    return names


@pytest.mark.parametrize("module", sorted(guarded_modules()))
def test_a_guarded_import_is_declared_in_requirements(module):
    """Being installed here is not enough: CI installs requirements.txt and
    nothing else, so a package that is present only because a developer once
    pulled it in as somebody's transitive dependency skips on CI and passes at
    home. That is the exact shape of both holes this file was written for."""
    package = module.split(".")[0]
    if package in ALLOWED_ABSENT:
        pytest.skip(ALLOWED_ABSENT[package])

    distributions = packages_distributions().get(package)
    if not distributions:
        pytest.skip(f"{package} reports no distribution to look for")

    declared = declared_distributions()
    where = ", ".join(sorted(guarded_modules()[module]))
    assert any(d.lower().replace("_", "-") in declared for d in distributions), (
        f"{where} needs {package} ({' or '.join(distributions)}), which requirements.txt "
        f"does not ask for — CI will skip those tests instead of running them"
    )


def test_the_allow_list_carries_a_reason_not_just_a_name():
    """An entry with an empty reason is a silenced test with no record of why."""
    unexplained = [name for name, reason in ALLOWED_ABSENT.items() if len(reason.split()) < 4]

    assert unexplained == [], f"allowed to be absent with no explanation: {unexplained}"
