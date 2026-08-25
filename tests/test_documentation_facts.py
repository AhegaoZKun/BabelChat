"""The documentation states facts. These are the ones a machine can check.

CLAUDE.md carried a table of twenty-one module line counts, of which sixteen
were wrong — it had been lying for longer than it had been right, and the file
itself said to verify the numbers before trusting them, which is a confession
rather than a fix. The table is gone; what remains here is the handful of claims
that can be checked automatically, so they cannot rot the same way.

Prose is not checked. A test that tried would either be trivial or would stop
anyone improving a sentence.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
READMES = ("README.md", "README_ru.md", "README_es.md")


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


#: CLAUDE.md is deliberately not committed — .gitignore lists it with the other
#: local dev-tool files — so it does not exist on CI. The checks that read it
#: are a safeguard for the machine where it is edited, and they skip with a
#: reason rather than passing vacuously in the one place nobody is looking.
claude_md = pytest.mark.skipif(
    not (ROOT / "CLAUDE.md").is_file(),
    reason="CLAUDE.md is gitignored (local dev-tool file) and absent here",
)


def declared_version() -> str:
    toc = (ROOT / "addon" / "BabelChat" / "BabelChat.toc").read_text(encoding="utf-8", errors="replace")
    found = re.search(r"^## Version:\s*([0-9.]+)", toc, re.MULTILINE)
    assert found, "the addon TOC declares no version"
    return found.group(1)


# ── the numbers ──────────────────────────────────────────────────────────────


def declared_test_floor() -> int:
    """What CI insists the suite collects.

    This is the one test count in the repository that is checked against a real
    run: the workflow compares it with junit's own figure and fails if the suite
    has shrunk. Counting `def test_` here instead would compare definitions
    against runs and be wrong by the whole of parametrisation — 670 against 998
    — which is how the first version of this test managed to fail on correct
    documentation.
    """
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    found = re.search(r'MIN_TESTS:\s*"(\d+)"', workflow)
    assert found, "the workflow no longer declares a test floor"
    return int(found.group(1))


def test_every_readme_states_the_test_count_ci_enforces():
    """Three files and CLAUDE.md all quote it, and they drifted apart: the
    READMEs said 795 while the suite ran 998."""
    floor = declared_test_floor()

    checked = [*READMES]
    if (ROOT / "CLAUDE.md").is_file():
        checked.append("CLAUDE.md")

    for name in checked:
        stated = re.search(r"(\d{3,5})\s*(?:tests|тестов|passing|\(pytest\))", read(name))
        assert stated, f"{name} states no test count"
        # Within a tenth, not exact. Exact would mean editing four files on
        # every commit that adds a test, and a rule that tedious gets worked
        # around rather than followed. A tenth still catches the drift that
        # happened: 795 against 998.
        assert abs(int(stated.group(1)) - floor) <= floor * 0.1, (
            f"{name} says {stated.group(1)} tests; CI insists on at least {floor}"
        )


@claude_md
def test_claude_md_no_longer_carries_a_table_of_line_counts():
    """Twenty-one numbers, sixteen wrong. Nothing keeps them honest and nobody
    reads them for anything a `wc -l` would not answer better."""
    body = read("CLAUDE.md")
    module_map = body[body.index("### Module Map") :]
    module_map = module_map[: module_map.index("### ", 10)]

    numbers = re.findall(r"\|\s*(\d{2,4})\s*\|", module_map)
    assert numbers == [], f"the module map has gone back to quoting line counts: {numbers}"


@claude_md
def test_the_module_count_is_right():
    """The one number in there that is worth stating, because it says how big
    the thing is."""
    actual = len(list((ROOT / "app").glob("*.py")))
    claimed = re.search(r"(\d+) модул", read("CLAUDE.md"))

    assert claimed, "CLAUDE.md no longer says how many modules there are"
    assert int(claimed.group(1)) == actual, f"CLAUDE.md says {claimed.group(1)}, there are {actual}"


@claude_md
def test_claude_md_states_the_version_the_addon_declares():
    body = read("CLAUDE.md")

    assert f"Version: {declared_version()}" in body, f"CLAUDE.md does not say {declared_version()}"


# ── the things that stopped being true ───────────────────────────────────────


@pytest.mark.parametrize("name", READMES)
def test_no_readme_still_describes_the_scanner_as_searching(name):
    """It searched for the buffer, and that was the whole performance problem:
    a Lua string is reallocated somewhere new on every rebuild. It reads through
    a pointer the addon parks now, and a reader told otherwise would go looking
    for a scan cost that is no longer there."""
    body = read(name)

    assert "Rayon" not in body, "still describes the parallel sweep as the mechanism"
    assert "pymem (ReadProcessMemory)" not in body, "still names the fallback as the Windows reader"


@pytest.mark.parametrize("name", READMES)
def test_no_readme_asks_for_administrator_rights(name):
    """The build stopped requesting them: ReadProcessMemory against a process
    owned by the same user never needed them, and standing elevation turned an
    ordinary DLL-planting bug into a privilege escalation."""
    body = read(name).lower()

    for phrase in ("run as administrator", "запустить от администратора", "ejecutar como administrador"):
        assert phrase not in body, f"{name} still tells the user to elevate"


@pytest.mark.parametrize(
    ("name", "phrase"),
    [
        ("docs/user/faq.md", "secret value"),
        ("docs/user/faq_ru.md", "секретным значением"),
        ("store-description.md", "secret value"),
        ("store-description.md", "секретным значением"),
    ],
)
def test_the_documents_a_player_reads_explain_the_keystone_silence(name, phrase):
    """Chat cannot be read during a Mythic+ key — the game hands addons a secret
    value, and no addon gets past that. A user who is not told will report it as
    a bug in this one, so every document a player actually opens says it, in the
    language that document is written in."""
    body = read(name)

    assert phrase in body, f"{name} does not explain why chat goes quiet in a key"


@pytest.mark.parametrize("name", ["docs/user/faq.md", "docs/user/faq_ru.md"])
def test_no_faq_still_says_the_reader_searches_memory(name):
    """It swept the heap for the buffer, and that sweep was the performance
    problem this release exists to remove. A user told to expect a search will
    also expect the cost of one."""
    body = read(name).lower()

    for phrase in ("scanning wow's memory", "сканирует память"):
        assert phrase not in body, f"{name} still describes the sweep as the mechanism"


@claude_md
def test_claude_md_records_what_was_tried_and_failed():
    """The dead-ends list is the part of this file that saves the most time,
    and three of its entries were learned expensively in one session."""
    body = read("CLAUDE.md")
    dead_ends = body[body.index("Dead ends") :]

    for what in ("Выравнивание длины буфера", "искать там, где буфер жил недавно", "указателю на саму строку"):
        assert what in dead_ends, f"the dead end {what!r} is not written down"
