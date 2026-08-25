"""Two changelogs that have to stay the same changelog.

They were one trilingual file, and it had rotted the way that arrangement
always does: the headings carried three languages while the entries under them
carried one, a duplicate title had grown in the middle, and orphaned Russian
and Spanish paragraphs sat below a horizontal rule belonging to nothing.

Split in two, the failure mode changes: a release note added to one file and
forgotten in the other. That is what this file watches for — nothing here
checks prose, only that neither file has quietly fallen behind.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGLISH = ROOT / "CHANGELOG.md"
RUSSIAN = ROOT / "CHANGELOG_ru.md"

VERSION = re.compile(r"^## \[([0-9]+\.[0-9]+\.[0-9]+)\] — (\d{4}-\d{2}-\d{2})$", re.MULTILINE)
CYRILLIC = re.compile(r"[а-яА-ЯёЁ]")
#: Spanish-only letters. Plain Latin text appears in both files — module names,
#: WoW terms, slash commands — so only these mark actual Spanish prose.
SPANISH = re.compile(r"[¿¡ñáéíóúü]", re.IGNORECASE)


def releases(path: pathlib.Path) -> list[tuple[str, str]]:
    return VERSION.findall(path.read_text(encoding="utf-8"))


# ── the two files describe the same releases ─────────────────────────────────


def test_both_files_list_the_same_versions_in_the_same_order():
    """A version added to one and forgotten in the other is the whole risk of
    keeping two files."""
    assert releases(ENGLISH) == releases(RUSSIAN)


def test_the_scan_finds_the_releases_that_are_there():
    """Comparing two empty lists passes, and would go on passing after either
    file was emptied."""
    found = releases(ENGLISH)

    assert len(found) >= 15, f"only {len(found)} releases parsed — the heading format has changed"
    assert found[0][0] > found[-1][0], "newest first"


def test_every_version_appears_exactly_once():
    """The old file had grown a second copy of its own title, and a version
    heading could just as easily be duplicated by a bad merge."""
    for path in (ENGLISH, RUSSIAN):
        versions = [version for version, _ in releases(path)]
        duplicated = sorted({v for v in versions if versions.count(v) > 1})
        assert duplicated == [], f"{path.name} lists these twice: {duplicated}"


def test_the_dates_agree():
    """A release cannot have happened on two days."""
    english = dict(releases(ENGLISH))
    russian = dict(releases(RUSSIAN))

    disagreeing = {v: (english[v], russian[v]) for v in english if english[v] != russian[v]}
    assert disagreeing == {}, f"different dates for: {disagreeing}"


# ── each file is in one language ─────────────────────────────────────────────


#: A quoted span: backticks, straight quotes or guillemets. What is inside one
#: is a cited example, not prose — the English file has to be able to say that
#: a sentence opening with "Спс" never met the key "спс", because that is the
#: defect being described.
QUOTED = re.compile(r"`[^`]*`|\"[^\"]*\"|«[^»]*»")


def prose(line: str) -> str:
    """The line with its quoted examples removed."""
    return QUOTED.sub(" ", line)


def test_the_english_changelog_is_written_in_english():
    """Cited Russian survives; Russian sentences do not. The old file carried
    both, and only one of them belongs here."""
    offending = [
        line
        for line in ENGLISH.read_text(encoding="utf-8").splitlines()
        if CYRILLIC.search(prose(line)) and "CHANGELOG_ru.md" not in line
    ]

    assert offending == [], f"Russian prose in the English changelog: {offending[:2]}"


def test_the_quote_stripping_does_not_swallow_the_whole_line():
    """If `prose` returned nothing the test above could not fail, and an
    entirely Russian file would read as clean English."""
    russian_sentence = "Подсказка была на испанском, и `targetLocale` стоял esES."

    assert CYRILLIC.search(prose(russian_sentence)), "quote stripping removed the prose as well"
    assert not CYRILLIC.search(prose('a sentence opening with "Спс" never met the key "спс"'))


def test_neither_changelog_carries_spanish():
    """Spanish was dropped: the app's interface still has it, the release
    history does not, and a third column nobody maintained was most of why the
    file had become unreadable."""
    for path in (ENGLISH, RUSSIAN):
        offending = [line for line in path.read_text(encoding="utf-8").splitlines() if SPANISH.search(line)]
        assert offending == [], f"Spanish in {path.name}: {offending[:3]}"


def test_no_heading_carries_more_than_one_language():
    """`### Added / Добавлено / Añadido` is the shape this split existed to
    remove, and it is the one a copied-in entry would bring back."""
    for path in (ENGLISH, RUSSIAN):
        offending = [
            line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("###") and "/" in line
        ]
        assert offending == [], f"multilingual headings in {path.name}: {offending[:3]}"


# ── and they point at each other ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "expected"),
    [(ENGLISH, "CHANGELOG_ru.md"), (RUSSIAN, "CHANGELOG.md")],
    ids=["english_links_to_russian", "russian_links_to_english"],
)
def test_each_file_links_to_the_other(path, expected):
    head = "\n".join(path.read_text(encoding="utf-8").splitlines()[:6])

    assert expected in head, f"{path.name} does not link to {expected} near the top"


def test_the_current_version_is_the_newest_entry():
    """A release that ships without its own changelog entry is the ordinary
    way these files fall out of date."""
    toc = (ROOT / "addon" / "BabelChat" / "BabelChat.toc").read_text(encoding="utf-8", errors="replace")
    match = re.search(r"^## Version:\s*([0-9]+\.[0-9]+\.[0-9]+)", toc, re.MULTILINE)
    assert match, "the addon TOC does not declare a version"

    newest = releases(ENGLISH)[0][0]
    assert match.group(1) == newest, f"the addon is {match.group(1)} and the changelog stops at {newest}"
