"""The TOC and the embedded libraries, checked against what is on disk and what
actually loads.

Two failure modes motivated this file. A library listed in the TOC but missing
from disk is invisible until a user installs the package. And LibBabble-ItemSet
ends with an `error()` for any locale it has no tables for — it shipped without
enGB, so every EU-English client threw a Lua error on login while every locale
the developers tested on was fine.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("lupa", reason="lupa provides the Lua 5.1 runtime the addon needs")

from lupa import lua51  # noqa: E402

ADDON_DIR = Path(__file__).resolve().parent.parent / "addon" / "BabelChat"
TOC = ADDON_DIR / "BabelChat.toc"

# Every locale a retail WoW client can report.
WOW_LOCALES = [
    "enUS",
    "enGB",
    "deDE",
    "esES",
    "esMX",
    "frFR",
    "itIT",
    "koKR",
    "ptBR",
    "ruRU",
    "zhCN",
    "zhTW",
]


def toc_files() -> list[str]:
    """File entries from the TOC, in load order (directives and comments dropped)."""
    entries = []
    for line in TOC.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        entries.append(line)
    return entries


def toc_libraries() -> set[str]:
    return {entry.replace("\\", "/").split("/")[1] for entry in toc_files() if entry.startswith("Libs\\")}


# ── the TOC matches the tree ─────────────────────────────────────────────────


def test_every_file_listed_in_the_toc_exists():
    missing = [e for e in toc_files() if not (ADDON_DIR / e.replace("\\", "/")).is_file()]
    assert missing == [], f"TOC lists files that are not on disk: {missing}"


def test_every_library_on_disk_is_listed_in_the_toc():
    """An unlisted library is dead weight: it ships in the package and never loads."""
    on_disk = {d.name for d in (ADDON_DIR / "Libs").iterdir() if d.is_dir()}
    assert on_disk - toc_libraries() == set(), "library directories present but never loaded"


def test_the_toc_loads_no_library_the_addon_never_calls():
    """LibBabble-Class, -Race and -CreatureType were loaded and never used — each
    one parsing thousands of lines at login and carrying its own locale error()."""
    source = "\n".join(path.read_text(encoding="utf-8") for path in ADDON_DIR.glob("*.lua"))
    # These are pulled in by other libraries rather than named in our own code.
    indirect = {"LibStub", "CallbackHandler-1.0", "LibBabble-3.0"}
    unused = {name for name in toc_libraries() - indirect if name not in source}
    assert unused == set(), f"libraries loaded by the TOC but referenced nowhere: {unused}"


def test_the_toc_opens_with_a_directive():
    """A TOC whose first line is a plain '#' comment silently drops every
    directive after it — a patch 12.0.7 bug."""
    first = TOC.read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith("## "), first


def test_the_interface_version_matches_the_client_that_is_installed():
    """Repeating the TOC's own number back at it proved nothing. The number has
    to match the installed client, which .build.info is the ground truth for —
    and where no client is installed there is nothing to check, so this skips
    rather than pretending."""
    from pathlib import Path

    build_info = next(
        (
            candidate
            for candidate in (
                Path("D:/World of Warcraft/.build.info"),
                Path("C:/Program Files (x86)/World of Warcraft/.build.info"),
            )
            if candidate.exists()
        ),
        None,
    )
    if build_info is None:
        pytest.skip("no WoW installation to compare against")

    text = build_info.read_text(encoding="utf-8", errors="replace")
    installed = re.findall(r"(\d+)\.(\d+)\.(\d+)\.\d+", text)
    if not installed:
        pytest.skip(".build.info carries no version this test recognises")

    major, minor, patch = max(installed, key=lambda v: tuple(int(part) for part in v))
    expected = f"{int(major)}{int(minor):02d}{int(patch):02d}"

    first = TOC.read_text(encoding="utf-8").splitlines()[0]
    versions = re.findall(r"\d{6}", first)

    assert versions, "no interface version in the TOC"
    assert versions[0] == expected, f"the client is {major}.{minor}.{patch}, the TOC targets {versions[0]}"


def test_the_interface_version_is_shaped_like_one():
    """Holds on any machine, including CI, where no client is installed."""
    first = TOC.read_text(encoding="utf-8").splitlines()[0]
    versions = re.findall(r"\d{6}", first)

    assert versions, "no interface version in the TOC"
    assert len(versions) == len(set(versions)), f"a version is listed twice: {versions}"
    assert versions == sorted(versions, reverse=True), "the current interface must be listed first"


# ── the libraries actually load, on every locale a client can report ─────────


def library_files(name: str) -> list[str]:
    """Every file the TOC loads for one library, in TOC order.

    LibBabble-SubZone keeps each locale in its own file, so loading only the
    base gives an English client its table and every other client nothing.
    Driving this off the TOC also means the test fails if a locale file is ever
    added to the tree but not to the load order.
    """
    prefix = "Libs\\" + name + "\\"
    return [e.replace("\\", "/") for e in toc_files() if e.startswith(prefix)]


def load_library(locale: str, *relative_paths: str):
    """Load the LibStub + LibBabble chain plus one library, on a stubbed locale.

    Three things the game's loader does for free and a bare Lua 5.1 does not:

    * these library files begin with a UTF-8 BOM, which Lua 5.1 rejects as a
      syntax error on line 1 — WoW strips it, so the harness does too;
    * WoW predefines string helpers like `strmatch` as globals, and LibStub
      calls one on its very first line;
    * `encoding=None` keeps lupa handing back raw bytes. These tables hold
      names in a dozen languages, and a decode failure while reporting an error
      would mask the error itself.
    """
    lua = lua51.LuaRuntime(unpack_returned_tuples=True, encoding=None)
    lua.execute(
        b"strmatch = string.match; strfind = string.find; strsub = string.sub;"
        b"strlower = string.lower; strupper = string.upper;"
        b"tinsert = table.insert; tremove = table.remove"
    )
    lua.globals().GetLocale = lambda: locale.encode()
    run = lua.execute(
        b"return function(src, name, addon, private)"
        b"  local chunk, err = loadstring(src, name)"
        b"  if not chunk then return 'LOAD FAILED: ' .. tostring(err) end"
        b"  local ok, runtime_err = pcall(chunk, addon, private)"
        b"  if ok then return 'OK' end"
        b"  return 'RAISED: ' .. tostring(runtime_err)"
        b"end"
    )
    private = lua.eval(b"{}")
    chain = (
        "Libs/LibStub/LibStub.lua",
        "Libs/LibBabble-3.0/LibBabble-3.0.lua",
        *relative_paths,
    )
    for relative in chain:
        source = (ADDON_DIR / relative).read_bytes().removeprefix(b"\xef\xbb\xbf")
        outcome = run(source, b"@" + relative.encode(), b"BabelChat", private)
        assert outcome == b"OK", f"{relative} on {locale}: {outcome.decode('utf-8', 'replace')}"
    return lua


@pytest.mark.parametrize("locale", WOW_LOCALES)
def test_item_set_library_loads_on_every_client_locale(locale):
    """It ends in `error("Locale %q not supported")`; enGB used to reach it."""
    load_library(locale, *library_files("LibBabble-ItemSet-3.0"))


@pytest.mark.parametrize("locale", WOW_LOCALES)
def test_sub_zone_library_loads_on_every_client_locale(locale):
    load_library(locale, *library_files("LibBabble-SubZone-3.0"))


@pytest.mark.parametrize("locale", WOW_LOCALES)
def test_no_locale_gets_a_silently_dead_toggle(locale):
    """The Zones and Item Sets checkboxes are only honest if the library behind
    them actually populated a table for this client's locale. An empty table
    means the toggle is on, the user believes it works, and nothing happens."""
    for library in ("LibBabble-SubZone-3.0", "LibBabble-ItemSet-3.0"):
        lua = load_library(locale, *library_files(library))
        table = lua.eval(f'LibStub("{library}"):GetUnstrictLookupTable()'.encode())
        assert table is not None, f"{library} has no table on {locale}"
        # A floor, not "not empty": one entry passes a >0 check while the
        # locale is in practice unusable, which is the failure this names.
        assert len(list(table)) > 50, f"{library} has only {len(list(table))} entries on {locale}"


@pytest.mark.parametrize("locale", ["enUS", "enGB"])
def test_english_clients_get_populated_zone_names(locale):
    """enGB loaded without error but with no current translations, which made the
    Zones toggle a silent no-op for every EU-English player."""
    lua = load_library(locale, *library_files("LibBabble-SubZone-3.0"))
    table = lua.eval(b'LibStub("LibBabble-SubZone-3.0"):GetUnstrictLookupTable()')
    assert table is not None, "no lookup table — the Zones toggle would do nothing"
    assert table[b"Elwynn Forest"] == b"Elwynn Forest"


@pytest.mark.parametrize("locale", ["enUS", "enGB"])
def test_english_clients_get_populated_item_set_names(locale):
    lua = load_library(locale, *library_files("LibBabble-ItemSet-3.0"))
    table = lua.eval(b'LibStub("LibBabble-ItemSet-3.0"):GetUnstrictLookupTable()')
    assert table is not None, "no lookup table — the Item Sets toggle would do nothing"
    assert len(list(table)) > 100, "an English client must get the full set list"


# ── documentation that claims a number ───────────────────────────────────────


def test_the_readme_term_count_is_the_number_of_terms_there_are():
    """It said 314 while the data files held 383. A number in a README is a
    claim, and a stale one is the kind a contributor trusts and repeats."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    actual = sum(
        len(re.findall(r'^\s*\["?[^\]]+"?\]\s*=\s*\{', path.read_text(encoding="utf-8"), re.M))
        for path in (root / "addon" / "BabelChat" / "Data").glob("*.lua")
    )

    for name in ("README.md", "README_ru.md"):
        text = (root / name).read_text(encoding="utf-8")
        counted = r"(\d{3}) (?:gaming terms|terms|игровых терминов|игровых термина|термина|терминов)"
        claimed = {int(n) for n in re.findall(counted, text)}
        assert claimed, f"{name} no longer states a term count — update this test with it"
        assert claimed == {actual}, f"{name} claims {sorted(claimed)}, the data files hold {actual}"


# ── directives the addon does not work without ───────────────────────────────


def toc_directive(name: str) -> str | None:
    for line in TOC.read_text(encoding="utf-8-sig").splitlines():
        if line.startswith(f"## {name}:"):
            return line.split(":", 1)[1].strip()
    return None


def test_the_saved_variable_the_companion_reads_is_declared():
    """The single line the whole companion protocol rests on. Without it WoW
    never persists BabelChatDB, so BabelChatDB.wctbuf never exists, the memory
    reader finds nothing, and the app looks broken with no error anywhere.

    Nothing covered it: renaming it left the entire suite green.
    """
    assert toc_directive("SavedVariables") == "BabelChatDB"


def test_every_lua_file_the_addon_ships_is_loaded_by_the_toc():
    """A file present on disk and absent from the TOC is dead code that looks
    live. Dropping DictEngine.lua from the load order left the suite green."""
    listed = {
        line.strip().replace("\\", "/")
        for line in TOC.read_text(encoding="utf-8-sig").splitlines()
        if line.strip().endswith(".lua")
    }
    root = TOC.parent
    on_disk = {
        str(path.relative_to(root)).replace("\\", "/")
        for path in root.rglob("*.lua")
        if "Libs" not in path.parts
    }

    unloaded = sorted(on_disk - listed)

    assert unloaded == [], f"shipped but never loaded: {unloaded}"


def test_the_engine_loads_after_the_data_it_indexes():
    """RebuildMasterDict reads addonTable.*Dict at load time, so a data file
    listed after DictEngine.lua contributes nothing and its category is silently
    empty."""
    order = [
        line.strip().replace("\\", "/")
        for line in TOC.read_text(encoding="utf-8-sig").splitlines()
        if line.strip().endswith(".lua")
    ]
    engine = order.index("DictEngine.lua")
    late_data = [name for name in order[engine:] if name.startswith("Data/")]

    assert late_data == [], f"loaded after the engine that indexes them: {late_data}"


def test_the_addon_and_the_app_report_the_same_version():
    """The addon is installed by hand, so `## Version` is the only way a user or
    a bug report can say which one is running — and the readers carry a compat
    branch keyed on a version number, which is only meaningful if the two agree.
    """
    from app.about_dialog import VERSION

    assert toc_directive("Version") == VERSION
