"""Reading the buffer through the addon's table instead of hunting for it.

The buffer is a Lua string, so every rebuild allocates a new one somewhere else.
Measured on a live game: fourteen consecutive rebuilds landed in fourteen
different regions, twenty gigabytes apart, never once returning to a region it
had used. So every design that searches for it pays a sweep per rebuild, and
the sweeps are what the scanner's whole history is made of — the shipped
release burned 48% of one core and delivered five messages a minute.

The addon parks a constant in its saved table. A constant can be searched for
at leisure, because it does not move while you look; and a Lua table's storage
does not move at all while the table does not rehash, which the addon prevents
by declaring every key at load. So the slot holding the buffer's pointer sits a
few dozen bytes from that constant, and reading it gives the current string.

Proven on the live game before any of it was written: one slot, six reads over
twelve seconds, six different string addresses, every one a valid buffer. After
it was written: 0.10% of one core, and zero sweeps.
"""

from __future__ import annotations

import pathlib
import re

SCANNER = pathlib.Path(__file__).resolve().parent.parent / "babelchat_scanner_win" / "src"
ADDON = pathlib.Path(__file__).resolve().parent.parent / "addon" / "BabelChat" / "CompanionBuffer.lua"


def scanner_source() -> str:
    """The whole crate, not one file of it.

    The scanner outgrew a single module and was split along its seams — the
    process it reads, the markers it reads for, the search, and the table slot.
    A check that read only lib.rs would quietly stop covering four fifths of
    what it is about."""
    return chr(10).join(path.read_text(encoding="utf-8") for path in sorted(SCANNER.glob("*.rs")))


def addon_source() -> str:
    return ADDON.read_text(encoding="utf-8")


# ── the two ends have to agree on the number ─────────────────────────────────


def test_the_addon_writes_an_anchor():
    body = addon_source()

    assert "wctAnchor" in body, "the addon parks nothing for the reader to find"
    found = re.search(r"db\.wctAnchor = (\d+)", body)
    assert found, "the anchor is not a plain constant"
    assert int(found.group(1)) > 10**9, "too small a number to be distinctive in a gigabyte of memory"


def test_the_scanner_looks_for_the_same_number():
    """Two constants in two languages, and nothing at compile time relates
    them. If they drift apart the reader finds nothing and falls back to
    sweeping the heap, which is exactly the behaviour being replaced."""
    addon = re.search(r"db\.wctAnchor = (\d+)", addon_source())
    scanner = re.search(r"const ANCHOR_VALUE: f64 = (\d+)\.0;", scanner_source())

    assert addon and scanner, "one side or the other has no anchor"
    assert addon.group(1) == scanner.group(1), (
        f"the addon parks {addon.group(1)} and the scanner hunts for {scanner.group(1)}"
    )


def test_the_anchor_is_declared_where_the_other_keys_are():
    """It has to be in the same table, and the table must not rehash after it —
    the slot's whole value is that it does not move."""
    body = addon_source()
    preallocate = body[body.index("function addonTable.PreallocateCompanionKeys") :]
    preallocate = preallocate[: preallocate.index("\nend")]

    assert "db.wctAnchor" in preallocate, "the anchor is set outside the preallocation"
    # And nowhere else. Setting it later would add a key to a table that has
    # already been sized, which is the rehash the preallocation exists to
    # prevent — and a rehash moves every slot, including the one the reader is
    # holding.
    assert body.count("wctAnchor") == 1, "the anchor is written in more than one place"


# ── and the reader must not settle for a dead table ──────────────────────────


def locate_anchor_body() -> str:
    """Just that one function. The same comparison appears in the sweep, and a
    check that found it there would pass with this one gutted."""
    source = scanner_source()
    start = source.index("fn locate_anchor(pid: u32)")
    end = source.index("#[cfg(not(windows))]", start)
    return source[start:end]


def test_every_candidate_is_weighed_not_just_the_first():
    """A reload leaves the previous table in memory until the collector gets to
    it, and the dead one answers every question the same way the live one does.
    Taking the first cost three minutes of silence, twice."""
    body = locate_anchor_body()

    assert "for anchor in anchors {" in body, "only one candidate is considered"
    assert "best.as_ref().is_none_or(|(b, _, _)| score > *b)" in body, "candidates are not compared against each other"
    assert "read_via_slot(handle, slot, skip)" in body, "candidates are judged without reading what they point at"


def test_a_slot_whose_pulse_stops_is_abandoned():
    """The pulse ticks whether or not anyone is talking, so a slot that has
    stopped is pointing at a table nobody writes to. Waiting for the pointer to
    break instead took three minutes; this takes six seconds."""
    source = scanner_source()

    assert "ANCHOR_MAX_QUIET_MS" in source
    quiet = re.search(r"const ANCHOR_MAX_QUIET_MS: u64 = ([0-9_]+);", source)
    assert quiet, "the limit is not a named constant"
    milliseconds = int(quiet.group(1).replace("_", ""))
    assert 3_000 <= milliseconds <= 20_000, f"{milliseconds}ms is not a sane limit"


def test_the_slot_is_tried_before_anything_else():
    """If the sweep runs first the saving is gone, and the sweep is the whole
    cost."""
    source = scanner_source()

    slot = source.index("// ── The slot, if we have one ──")
    fast = source.index("// ── Fast path ──")
    slow = source.index("// ── Slow path")

    assert slot < fast < slow, "the slot is no longer the first thing tried"


def test_the_sweep_is_still_there_for_an_addon_without_an_anchor():
    """The app is updated separately from the addon, by hand, from a zip. One
    running ahead of the other has to keep working, slowly."""
    source = scanner_source()

    assert "fn full_scan" in source
    assert "get_readable_regions" in source


def test_the_state_report_says_which_slot():
    """Every hour of this was spent inferring the scanner's situation from
    outside. It says so now."""
    source = scanner_source()

    assert "slot=0x{:x}+{}" in source or "slot=" in source


# ── the pulse the whole thing leans on ───────────────────────────────────────


def test_the_addon_keeps_the_pulse_ticking_when_nothing_is_said():
    """A pulse that only moved when a message arrived would leave a dead table
    indistinguishable from a quiet one, which is the confusion every version of
    this has died of."""
    body = addon_source()

    assert "HEARTBEAT_INTERVAL" in body
    assert "GetTime() - lastRebuild) >= HEARTBEAT_INTERVAL" in body, (
        "an idle chat no longer rebuilds, so the pulse stops in it"
    )


def test_the_padding_experiment_left_nothing_behind():
    """Padding the buffer to a fixed length was meant to stop it moving, on the
    theory that the allocator would reuse the block. It does not — nineteen
    addresses in two minutes — because Lua interns strings and the old one is
    still alive when the new one is asked for. Disproven, so removed."""
    body = addon_source()

    assert "BUFFER_PAD_TO" not in body
    assert "string.rep" not in body


# ── kernel objects the scans used to leak ────────────────────────────────────


def test_the_parallel_scans_own_their_worker_handles():
    """Both scans open one process handle per worker thread — thousands of
    regions against four workers, so one per region would be far worse — and
    neither closed them. Four kernel objects per scan, and the scanner this
    replaced scanned continuously: enough over a long session to exhaust the
    handle table, at which point OpenProcess starts refusing and the app goes
    deaf with nothing to say about why.

    Owning the handle is the only version that cannot be forgotten the next
    time somebody adds a worker."""
    source = scanner_source()

    assert "struct OwnedHandle" in source, "nothing closes a handle on its own"
    assert "impl Drop for OwnedHandle" in source, "the owner does not actually close it"
    assert source.count("|| owned_process(pid),") == 2, "a parallel scan still opens a bare handle per worker"
    assert "|| open_process(pid).map(|h| h.0 as isize)," not in source, "the leaking pattern is still there"
